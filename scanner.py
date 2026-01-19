import streamlit as st
import pandas as pd
import requests
import anthropic
import json
import pytz
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

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
if "market_type" not in st.session_state:
    st.session_state.market_type = "Krypto"
if "trading_session" not in st.session_state:
    st.session_state.trading_session = "Regular"
if "active_trading_session" not in st.session_state:
    st.session_state.active_trading_session = "Regular"
if "watchlist" not in st.session_state:
    st.session_state.watchlist = []
if "sr_levels" not in st.session_state:
    st.session_state.sr_levels = {"support": [], "resistance": []}
if "fib_info" not in st.session_state:
    st.session_state.fib_info = {}
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = False

# =============================================================================
# 2. STRATEGIE-DEFINITIONEN
# =============================================================================
STRATEGIES = {
    "Volume Surge": {
        "description": "Aktien/Krypto mit überdurchschnittlichem Volumen",
        "filters": {"RVOL": (2.0, 50.0)},
        "logic": "RVOL > 2.0 zeigt erhöhtes Interesse"
    },
    "Bull Flag": {
        "description": "Konsolidierung nach starkem Anstieg - Volumen nimmt ab",
        "filters": {"Vortag %": (4.0, 25.0), "Change %": (-2.0, 2.0), "RVOL": (0.3, 1.5)},
        "logic": "Vortag stark positiv, heute seitwärts, Volumen sinkt = Bullflag"
    },
    "Bear Flag": {
        "description": "Konsolidierung nach Abverkauf - Short-Setup",
        "filters": {"Vortag %": (-25.0, -4.0), "Change %": (-2.0, 2.0), "RVOL": (0.3, 1.5)},
        "logic": "Vortag stark negativ, heute seitwärts, Volumen sinkt = Bearflag"
    },
    "Breakout Long": {
        "description": "Momentum-Ausbruch mit Volumen-Bestätigung",
        "filters": {"Change %": (5.0, 50.0), "RVOL": (2.0, 50.0), "Close Position": (0.75, 1.0)},
        "logic": "Starker Anstieg + hohes Volumen + Close nahe High"
    },
    "Breakdown Short": {
        "description": "Abverkauf mit Volumen - Short-Chance",
        "filters": {"Change %": (-50.0, -5.0), "RVOL": (2.0, 50.0), "Close Position": (0.0, 0.25)},
        "logic": "Starker Abverkauf + hohes Volumen + Close nahe Low"
    },
    "Penny Rockets": {
        "description": "Günstige Coins/Aktien mit explosivem Volumen",
        "filters": {"Preis": (0.0001, 1.0), "RVOL": (3.0, 100.0), "Change %": (2.0, 100.0)},
        "logic": "Lowcaps unter $1 mit extremem Interesse"
    },
    "Dip Buy": {
        "description": "Qualitäts-Assets im Rücksetzer ohne Panik",
        "filters": {"Preis": (10.0, 100000.0), "Change %": (-8.0, -2.0), "RVOL": (0.5, 2.0)},
        "logic": "Moderater Rücksetzer ohne Volumen-Panik = Kaufchance"
    },
    "Reversal Hunter": {
        "description": "Trendumkehr nach starkem Abverkauf",
        "filters": {"Vortag %": (-50.0, -5.0), "Change %": (2.0, 30.0), "RVOL": (1.5, 50.0)},
        "logic": "Gestern Crash, heute Käufer = potenzielle Umkehr"
    },
    "Early Momentum": {
        "description": "Starker Tagesstart mit Volumen",
        "filters": {"Change %": (3.0, 30.0), "RVOL": (1.5, 50.0)},
        "logic": "Positive Bewegung mit überdurchschnittlichem Volumen"
    },
    "Whale Watch": {
        "description": "Extremes Volumen - Big Player aktiv",
        "filters": {"RVOL": (5.0, 100.0)},
        "logic": "RVOL > 5.0 = institutionelles Interesse wahrscheinlich"
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
    "AH Earnings Movers 🌙": {
        "description": "🌙 AFTER-HOURS: Starke Bewegung (Earnings Season)",
        "filters": {"Change %": (8.0, 200.0), "Preis": (10.0, 1000.0)},
        "logic": ">8% Move nach Close = wahrscheinlich Earnings Reaction",
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
        "filters": {"Upper Wick %": (30.0, 100.0), "Change %": (-10.0, 5.0)},
        "logic": "Lange obere Wick zeigt Ablehnung höherer Preise = Short-Signal"
    },
    "Long Wick Down": {
        "description": "Lange untere Wick = Kaufdruck, oft Reversal nach oben",
        "filters": {"Lower Wick %": (30.0, 100.0), "Change %": (-3.0, 15.0)},
        "logic": "Lange untere Wick zeigt Ablehnung tieferer Preise = Long-Signal"
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
    # AKKUMULATIONS-STRATEGIEN 📦 - Wyckoff-Style
    # =========================================================================
    "Accumulation 📦": {
        "description": "📦 Wyckoff: Lange Seitwärtsphase mit abnehmendem Volumen - Breakout kommt!",
        "filters": {"Change %": (-3.0, 3.0), "Vortag %": (-3.0, 3.0), "RVOL": (0.3, 1.5)},
        "logic": "Enge Range + niedriges Volumen = Smart Money akkumuliert leise",
        "needs_history": True
    },
    "Accumulation Breakout 🚀": {
        "description": "📦→🚀 Breakout aus Akkumulationsphase mit Volumen-Bestätigung",
        "filters": {"Change %": (3.0, 30.0), "Vortag %": (-5.0, 5.0), "RVOL": (1.5, 50.0)},
        "logic": "Nach langer Ruhe plötzlich Ausbruch + Volumen = GO!"
    },
    "Spring Setup 🪤": {
        "description": "📦 Wyckoff Spring: Fakeout unter Support dann Recovery - Klassiker!",
        "filters": {"Change %": (2.0, 15.0), "Vortag %": (-8.0, -2.0), "RVOL": (1.2, 10.0)},
        "logic": "Gestern Dump (Spring), heute Recovery mit Volumen = Bullish Trap für Shorts"
    },
    "Tight Range 📐": {
        "description": "📐 Extrem enge Tagesrange - Explosion steht bevor (Richtung unklar)",
        "filters": {"Change %": (-1.5, 1.5), "RVOL": (0.3, 1.5)},
        "logic": "Wenn Volatilität extrem niedrig → folgt oft große Bewegung"
    },
    "Distribution 📤": {
        "description": "📤 Mögliche Distribution: Hohes Volumen + Close in oberer Range-Hälfte",
        "filters": {"Change %": (-5.0, 5.0), "RVOL": (2.0, 50.0), "Close Position": (0.5, 0.9)},
        "logic": "Hohes Volumen ohne Fortschritt am Top = Smart Money verteilt an Retail"
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
}

# =============================================================================
# FUTURES STRATEGIEN 📈
# =============================================================================
FUTURES_STRATEGIES = {
    # =========================================================================
    # MOMENTUM STRATEGIEN
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
        "description": "🔄 Trendumkehr nach starkem Move",
        "filters": {"Vortag %": (-10.0, -2.0), "Change %": (0.5, 10.0)},
        "logic": "Gestern gefallen, heute steigend = potenzielle Umkehr"
    },
    # =========================================================================
    # SESSION-BASIERTE STRATEGIEN
    # =========================================================================
    "Globex Gap 🌙": {
        "description": "🌙 Overnight Gap vs. Regular Session Close",
        "filters": {"Change %": (0.3, 10.0)},
        "logic": "Gap zwischen US Close und Asia/Europe Session"
    },
    "London Open Momentum 🇬🇧": {
        "description": "🇬🇧 Momentum bei London Börsenöffnung (08:00 UTC)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "Europa-Session bringt oft neue Richtung"
    },
    "NY Open Breakout 🗽": {
        "description": "🗽 Breakout bei US-Börsenöffnung (14:30 UTC)",
        "filters": {"Change %": (0.3, 10.0)},
        "logic": "US-Session mit höchster Liquidität = große Moves"
    },
    # =========================================================================
    # SPREAD & STRUKTUR
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
    # =========================================================================
    # PIP-BASIERTE MOMENTUM STRATEGIEN
    # =========================================================================
    "Forex Momentum 💹": {
        "description": "💹 Starke Pip-Bewegung in eine Richtung",
        "filters": {"Change %": (0.3, 5.0)},
        "logic": "Für Forex ist >0.3% bereits signifikant"
    },
    "Forex Reversal 🔄": {
        "description": "🔄 Gegenbewegung nach starkem Vortag",
        "filters": {"Vortag %": (-3.0, -0.5), "Change %": (0.1, 3.0)},
        "logic": "Gestern gefallen, heute steigend = Umkehr-Signal"
    },
    "Pip Hunter 🎯": {
        "description": "🎯 Größte Pip-Bewegungen des Tages",
        "filters": {"Change %": (0.5, 10.0)},
        "logic": "Top Movers nach Pips sortiert"
    },
    # =========================================================================
    # SESSION-STRATEGIEN
    # =========================================================================
    "Tokyo Session 🇯🇵": {
        "description": "🇯🇵 Bewegungen während Tokyo Session (00:00-09:00 UTC)",
        "filters": {"Change %": (0.1, 3.0)},
        "logic": "Asiatische Session - oft ruhiger, aber JPY-Paare aktiv"
    },
    "London Session 🇬🇧": {
        "description": "🇬🇧 Bewegungen während London Session (08:00-17:00 UTC)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "Höchste Liquidität - EUR/GBP-Paare besonders aktiv"
    },
    "NY Session 🗽": {
        "description": "🗽 Bewegungen während NY Session (13:00-22:00 UTC)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "USD-Paare am aktivsten"
    },
    "London/NY Overlap 🔥": {
        "description": "🔥 Höchste Volatilität: London + NY gleichzeitig (13:00-17:00 UTC)",
        "filters": {"Change %": (0.3, 10.0)},
        "logic": "Beste Trading-Zeit - maximale Liquidität und Bewegung"
    },
    # =========================================================================
    # SPEZIELLE FOREX-STRATEGIEN
    # =========================================================================
    "Safe Haven Flow 🛡️": {
        "description": "🛡️ Flucht in sichere Währungen (CHF, JPY)",
        "filters": {"Change %": (-5.0, -0.2)},
        "logic": "USD/CHF oder USD/JPY fallen = Risk-Off Modus"
    },
    "Risk-On Rally 🚀": {
        "description": "🚀 Risikofreudige Währungen steigen (AUD, NZD)",
        "filters": {"Change %": (0.2, 5.0)},
        "logic": "AUD/USD, NZD/USD steigen = Risk-On Sentiment"
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
# =============================================================================
CRYPTO_STRATEGIES = {
    "Volume Surge": {
        "description": "Extremes Volumen + starke Bewegung",
        "filters": {"RVOL": (2.0, 100.0), "Change %": (3.0, 100.0)},
        "logic": "RVOL > 2.0 UND Change > 3%"
    },
    "Bull Flag": {
        "description": "Bullische Konsolidierung nach Anstieg",
        "filters": {"Vortag %": (4.0, 25.0), "Change %": (-2.0, 2.0), "RVOL": (0.3, 1.5)},
        "logic": "Vortag +4-25%, heute flach mit sinkendem Volumen"
    },
    "Bear Flag": {
        "description": "Bärische Konsolidierung nach Abverkauf",
        "filters": {"Vortag %": (-25.0, -4.0), "Change %": (-2.0, 2.0), "RVOL": (0.3, 1.5)},
        "logic": "Vortag -4-25%, heute flach = weitere Schwäche"
    },
    "Breakout Long": {
        "description": "Ausbruch nach oben mit Volumen",
        "filters": {"Change %": (5.0, 50.0), "RVOL": (2.0, 50.0), "Close Position": (0.7, 1.0)},
        "logic": "Close nahe High + hohes Volumen = Stärke"
    },
    "Breakdown Short": {
        "description": "Ausbruch nach unten mit Volumen",
        "filters": {"Change %": (-50.0, -5.0), "RVOL": (2.0, 50.0), "Close Position": (0.0, 0.3)},
        "logic": "Close nahe Low + hohes Volumen = Schwäche"
    },
    "Penny Rockets 🚀": {
        "description": "Günstige Coins mit explosivem Volumen",
        "filters": {"Preis": (0.0001, 1.0), "RVOL": (3.0, 100.0), "Change %": (2.0, 100.0)},
        "logic": "Lowcaps unter $1 mit extremem Interesse"
    },
    "Dip Buy": {
        "description": "Qualitäts-Assets im Rücksetzer ohne Panik",
        "filters": {"Preis": (10.0, 100000.0), "Change %": (-8.0, -2.0), "RVOL": (0.5, 2.0)},
        "logic": "Moderater Rücksetzer ohne Volumen-Panik"
    },
    "Reversal Hunter": {
        "description": "Trendumkehr nach starkem Abverkauf",
        "filters": {"Vortag %": (-50.0, -5.0), "Change %": (2.0, 30.0), "RVOL": (1.5, 50.0)},
        "logic": "Gestern Crash, heute Käufer"
    },
    "Early Momentum": {
        "description": "Starke Bewegung mit Volumen",
        "filters": {"Change %": (3.0, 30.0), "RVOL": (1.5, 50.0)},
        "logic": "Positive Bewegung mit überdurchschnittlichem Volumen"
    },
    "Whale Watch 🐋": {
        "description": "Extremes Volumen - Big Player aktiv",
        "filters": {"RVOL": (5.0, 100.0)},
        "logic": "RVOL > 5.0 = institutionelles Interesse"
    },
    "Accumulation 📦": {
        "description": "Leise Akkumulation bei stabilem Preis",
        "filters": {"Change %": (-2.0, 2.0), "RVOL": (1.5, 5.0)},
        "logic": "Seitwärts + erhöhtes Volumen = jemand sammelt"
    },
}

# Funktion um Strategien basierend auf Markt zu bekommen
def get_strategies_for_market(market_type):
    """Gibt die passenden Strategien für den gewählten Markt zurück"""
    if market_type == "Krypto":
        return CRYPTO_STRATEGIES
    elif market_type == "Futures":
        return FUTURES_STRATEGIES
    elif market_type == "Forex":
        return FOREX_STRATEGIES
    else:  # Aktien
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
            
    except Exception:
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
        else:
            strategies_dict = STRATEGIES  # Aktien als Fallback
    
    if strategy_name in strategies_dict:
        strategy = strategies_dict[strategy_name]
        st.session_state.active_filters = strategy["filters"].copy()
        st.session_state.current_strategy = strategy_name
        st.session_state.additional_filters = {
            "preis_min": 0.0, "preis_max": 100000.0,
            "nur_gewinner": False, "nur_verlierer": False,
            "rvol_override_min": None, "rvol_override_max": None,
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

def calculate_close_position(high, low, close):
    if high == low or high is None or low is None:
        return 0.5
    return (close - low) / (high - low)

def calculate_alpha_score(rvol, vortag_pct, change_pct):
    return round((rvol * 12) + (abs(vortag_pct) * 10) + (abs(change_pct) * 8), 2)

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
        
    except Exception:
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

def calculate_volume_profile(ohlcv_data, num_bins=20):
    """
    Berechnet Volume Profile aus historischen OHLCV Daten.
    
    Volume Profile zeigt wie viel Volumen auf welchem Preisniveau gehandelt wurde.
    
    Args:
        ohlcv_data: Liste von dicts mit 'high', 'low', 'close', 'volume'
        num_bins: Anzahl der Preis-Zonen (default 20)
    
    Returns:
        dict mit:
        - bins: Liste von (price_low, price_high, volume) Tuples
        - poc: Point of Control (Preis mit meistem Volumen)
        - vah: Value Area High
        - val: Value Area Low
        - lvns: Liste von Low Volume Nodes
        - hvns: Liste von High Volume Nodes
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
            
            resp = requests.get(url, params=params, timeout=10)
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
            
        except Exception:
            continue
    
    # Sortiere nach Void Score
    results.sort(key=lambda x: x['void_score'], reverse=True)
    
    return results

def add_to_watchlist(ticker, data):
    """Fügt Ticker zur Watchlist hinzu"""
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
        return True
    return False

def remove_from_watchlist(ticker):
    """Entfernt Ticker von Watchlist"""
    st.session_state.watchlist = [w for w in st.session_state.watchlist if w["ticker"] != ticker]

def fetch_historical_data_crypto(coin_id, days):
    """Holt historische OHLC-Daten von CoinGecko"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
        params = {"vs_currency": "usd", "days": days}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # Format: [[timestamp, open, high, low, close], ...]
            if data and len(data) > 0:
                return data
    except:
        pass
    return None

def fetch_historical_data_stocks(ticker, days, poly_key):
    """Holt historische Daten von Polygon"""
    try:
        from datetime import timedelta
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
        params = {"apiKey": poly_key, "limit": days}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                # Format anpassen: [[timestamp, open, high, low, close], ...]
                return [[r["t"], r["o"], r["h"], r["l"], r["c"]] for r in results]
    except:
        pass
    return None

def calculate_sr_from_historical(ohlc_data, current_price):
    """Berechnet S/R-Levels aus Fibonacci + Swing Highs/Lows + Konsolidierungszonen"""
    if not ohlc_data or len(ohlc_data) < 5:
        return calculate_sr_levels_simple(current_price)
    
    # Extrahiere OHLC Daten
    highs = [candle[2] for candle in ohlc_data]  # Index 2 = High
    lows = [candle[3] for candle in ohlc_data]   # Index 3 = Low
    closes = [candle[4] for candle in ohlc_data] # Index 4 = Close
    
    # Periode High und Low (wichtig für Fibonacci)
    period_high = max(highs)
    period_low = min(lows)
    price_range = period_high - period_low
    
    if price_range <= 0:
        return calculate_sr_levels_simple(current_price), {}
    
    # =========================================================================
    # KONSOLIDIERUNGSZONEN BERECHNEN
    # Finde Preiszonen wo der Preis oft war (High Activity Zones)
    # =========================================================================
    
    # Teile den Preisbereich in Zonen auf
    num_zones = 20  # 20 Zonen über den Preisbereich
    zone_size = price_range / num_zones
    zone_counts = {}  # zone_start -> anzahl_tage
    
    for i, close in enumerate(closes):
        # Welche Zone ist dieser Close?
        zone_idx = int((close - period_low) / zone_size)
        zone_idx = min(zone_idx, num_zones - 1)  # Clamp
        zone_start = period_low + zone_idx * zone_size
        zone_end = zone_start + zone_size
        
        zone_key = (round(zone_start, 6), round(zone_end, 6))
        zone_counts[zone_key] = zone_counts.get(zone_key, 0) + 1
    
    # Sortiere nach Häufigkeit (meiste Tage zuerst)
    sorted_zones = sorted(zone_counts.items(), key=lambda x: x[1], reverse=True)
    
    # Top Konsolidierungszonen (min 3 Tage in der Zone)
    consolidation_zones = []
    total_candles = len(closes)
    
    for (zone_start, zone_end), count in sorted_zones[:5]:  # Top 5
        if count >= 3:  # Mindestens 3 Kerzen in dieser Zone
            pct_time = round((count / total_candles) * 100, 1)
            zone_mid = (zone_start + zone_end) / 2
            consolidation_zones.append({
                "low": zone_start,
                "high": zone_end,
                "mid": zone_mid,
                "days": count,
                "pct_time": pct_time
            })
    
    # Merge überlappende Zonen
    def merge_zones(zones):
        if not zones:
            return []
        zones = sorted(zones, key=lambda x: x["low"])
        merged = [zones[0]]
        for zone in zones[1:]:
            last = merged[-1]
            if zone["low"] <= last["high"] * 1.02:  # 2% Überlappung erlaubt
                # Merge
                merged[-1] = {
                    "low": last["low"],
                    "high": max(last["high"], zone["high"]),
                    "mid": (last["low"] + max(last["high"], zone["high"])) / 2,
                    "days": last["days"] + zone["days"],
                    "pct_time": last["pct_time"] + zone["pct_time"]
                }
            else:
                merged.append(zone)
        return merged
    
    consolidation_zones = merge_zones(consolidation_zones)[:3]  # Max 3 Zonen
    
    # =========================================================================
    # FIBONACCI LEVELS berechnen
    # =========================================================================
    fib_levels = {
        "0.0": period_low,
        "23.6": period_low + price_range * 0.236,
        "38.2": period_low + price_range * 0.382,
        "50.0": period_low + price_range * 0.5,
        "61.8": period_low + price_range * 0.618,
        "78.6": period_low + price_range * 0.786,
        "100.0": period_high,
        "127.2": period_high + price_range * 0.272,
        "161.8": period_high + price_range * 0.618,
    }
    
    # =========================================================================
    # SWING HIGHS/LOWS finden
    # =========================================================================
    swing_highs = []
    window = min(3, len(highs) // 4)
    for i in range(window, len(highs) - window):
        is_swing = True
        for j in range(1, window + 1):
            if highs[i] <= highs[i-j] or highs[i] <= highs[i+j]:
                is_swing = False
                break
        if is_swing:
            swing_highs.append(highs[i])
    
    swing_lows = []
    for i in range(window, len(lows) - window):
        is_swing = True
        for j in range(1, window + 1):
            if lows[i] >= lows[i-j] or lows[i] >= lows[i+j]:
                is_swing = False
                break
        if is_swing:
            swing_lows.append(lows[i])
    
    swing_highs.append(period_high)
    swing_lows.append(period_low)
    swing_highs = sorted(set(swing_highs), reverse=True)
    swing_lows = sorted(set(swing_lows))
    
    # =========================================================================
    # SUPPORTS & RESISTANCES kombinieren
    # =========================================================================
    all_supports = []
    all_resistances = []
    
    # Swing Lows
    for sl in swing_lows:
        if sl < current_price:
            all_supports.append({"price": sl, "type": "Swing Low"})
    
    # Fibonacci unter Preis
    for fib_name, fib_price in fib_levels.items():
        if fib_price < current_price and float(fib_name) <= 100:
            all_supports.append({"price": fib_price, "type": f"Fib {fib_name}%"})
    
    # Swing Highs
    for sh in swing_highs:
        if sh > current_price:
            all_resistances.append({"price": sh, "type": "Swing High"})
    
    # Fibonacci über Preis
    for fib_name, fib_price in fib_levels.items():
        if fib_price > current_price:
            all_resistances.append({"price": fib_price, "type": f"Fib {fib_name}%"})
    
    # Sortieren
    all_supports = sorted(all_supports, key=lambda x: x["price"], reverse=True)
    all_resistances = sorted(all_resistances, key=lambda x: x["price"])
    
    # Cluster-Bereinigung
    def remove_clusters(levels, min_distance_pct=2.0):
        if not levels:
            return []
        cleaned = [levels[0]]
        for level in levels[1:]:
            last_price = cleaned[-1]["price"]
            distance_pct = abs(level["price"] - last_price) / last_price * 100
            if distance_pct >= min_distance_pct:
                cleaned.append(level)
        return cleaned
    
    supports_cleaned = remove_clusters(all_supports)[:3]
    resistances_cleaned = remove_clusters(all_resistances)[:3]
    
    supports = [s["price"] for s in supports_cleaned]
    resistances = [r["price"] for r in resistances_cleaned]
    
    # Smart Rounding
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
            return round(price, 6)
    
    supports = [smart_round(s) for s in supports]
    resistances = [smart_round(r) for r in resistances]
    
    # Runde Konsolidierungszonen
    for zone in consolidation_zones:
        zone["low"] = smart_round(zone["low"])
        zone["high"] = smart_round(zone["high"])
        zone["mid"] = smart_round(zone["mid"])
    
    # =========================================================================
    # FIB INFO für AI-Analyse
    # =========================================================================
    fib_info = {
        "period_high": smart_round(period_high),
        "period_low": smart_round(period_low),
        "fib_236": smart_round(fib_levels["23.6"]),
        "fib_382": smart_round(fib_levels["38.2"]),
        "fib_500": smart_round(fib_levels["50.0"]),
        "fib_618": smart_round(fib_levels["61.8"]),
        "fib_786": smart_round(fib_levels["78.6"]),
        "fib_1272": smart_round(fib_levels["127.2"]),
        "fib_1618": smart_round(fib_levels["161.8"]),
        "supports_detail": supports_cleaned,
        "resistances_detail": resistances_cleaned,
        "consolidation_zones": consolidation_zones,  # NEU!
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
    
    # Timeframe zu Tagen mappen
    tf_to_days = {
        "1H": 1,
        "4H": 7,
        "1D": 30,
        "1W": 90,
        "1M": 180
    }
    days = tf_to_days.get(timeframe, 7)
    
    # Versuche historische Daten zu holen
    ohlc_data = None
    
    if market_type == "Krypto" and ticker:
        coin_id = ticker.lower()
        ohlc_data = fetch_historical_data_crypto(coin_id, days)
    
    elif market_type == "Aktien" and ticker and poly_key:
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
        elif market_type == "Aktien" and poly_key:
            ohlc_data = fetch_historical_data_stocks(ticker, days, poly_key)
        
        if not ohlc_data or len(ohlc_data) < 10:
            result["interpretation"] = "Nicht genug historische Daten"
            return result
        
        result["data_available"] = True
        
        # Daten extrahieren
        closes = [d["close"] for d in ohlc_data]
        highs = [d["high"] for d in ohlc_data]
        lows = [d["low"] for d in ohlc_data]
        volumes = [d.get("volume", 0) for d in ohlc_data]
        
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
                resp = requests.get(url, params=params, timeout=5)
                
                if resp.status_code == 200:
                    data = resp.json()
                    transactions = data.get("data", [])
                    
                    if transactions:
                        insider_data[ticker] = transactions
                        
            except:
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
    """Holt Krypto-Daten von CoinGecko mit korrektem Vortag"""
    results = []
    skipped_filter = 0
    
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd", 
            "order": "market_cap_desc",
            "per_page": 250, 
            "page": 1, 
            "sparkline": False,
            # Hole 24h UND 7d change
            "price_change_percentage": "24h,7d"
        }
        
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 429:
            st.warning("⚠️ CoinGecko Rate Limit. Warte 60 Sekunden.")
            return [], 0, 0
        
        coins = resp.json()
        if not isinstance(coins, list):
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
                
                # VORTAG BERECHNUNG:
                # CoinGecko kann 7d unter verschiedenen Namen liefern
                change_7d = (
                    coin.get("price_change_percentage_7d_in_currency") or 
                    coin.get("price_change_percentage_7d") or 
                    0
                )
                
                # Berechne Vortag aus 7d-Daten
                if change_7d != 0:
                    # Durchschnittliche tägliche Änderung der letzten 7 Tage
                    avg_daily_7d = change_7d / 7
                    # Vortag ≈ Durchschnitt (einfacher und stabiler)
                    vortag_chg = round(avg_daily_7d, 2)
                else:
                    # Fallback: Setze auf 0
                    vortag_chg = 0
                
                high_24h = coin.get("high_24h") or price
                low_24h = coin.get("low_24h") or price
                vol_24h = coin.get("total_volume") or 0
                market_cap = coin.get("market_cap") or 1
                
                # OHLC für Wick-Berechnung
                # Approximation: Open = Price / (1 + change/100)
                open_price = price / (1 + change_24h / 100) if change_24h != -100 else price
                
                # Wick-Berechnungen (KORREKT für Krypto)
                candle_range = high_24h - low_24h if high_24h > low_24h else 0.0001
                body_top = max(open_price, price)
                body_bottom = min(open_price, price)
                
                # Upper Wick %: (High - Body Top) / Candle Range * 100
                upper_wick_pct = ((high_24h - body_top) / candle_range) * 100 if candle_range > 0 else 0
                
                # Lower Wick %: (Body Bottom - Low) / Candle Range * 100
                lower_wick_pct = ((body_bottom - low_24h) / candle_range) * 100 if candle_range > 0 else 0
                
                # GAP % - KRYPTO HAT KEINE ECHTEN GAPS (24/7 Markt)
                # Wir setzen es auf None damit der Filter weiß dass es nicht anwendbar ist
                gap_pct = None  # Explizit None für "nicht verfügbar"
                
                # RVOL Berechnung (Krypto-spezifisch)
                if market_cap > 0:
                    vol_ratio = (vol_24h / market_cap) * 100
                    rvol = round(vol_ratio * 5, 2)
                    rvol = max(0.1, min(rvol, 100))
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
                    if af.get("rvol_override_min"): rvol_min = af["rvol_override_min"]
                    if af.get("rvol_override_max"): rvol_max = af["rvol_override_max"]
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
                
                # Close Position
                if "Close Position" in f and not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]): 
                    match = False
                
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
                    "ClosePos": round(close_pos, 2), 
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
            except:
                continue
        
        return results, 0, skipped_filter
    except Exception as e:
        st.error(f"CoinGecko Fehler: {e}")
        return [], 0, 0


def fetch_stock_data(poly_key, session="Regular"):
    """
    Holt Aktien-Daten von Polygon.io Snapshot API.
    
    WICHTIG: Die Snapshot API liefert immer die aktuellsten Daten inkl. Pre/Post Market
    im 'lastTrade' und 'min' Feld. Die 'day' Daten sind die Regular Session.
    
    Session Parameter steuert wie wir die Daten interpretieren:
    - Regular: Nutze 'day' Daten (Regular Hours OHLCV)
    - Pre-Market/After-Hours/Extended: Nutze 'lastTrade' für aktuellen Preis
    """
    results = []
    skipped_no_price = 0
    skipped_filter = 0
    
    try:
        # Polygon Snapshot API
        url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
        resp = requests.get(url, timeout=30).json()
        tickers = resp.get("tickers", [])
        
        if len(tickers) == 0:
            return [], 0, 0
        
        f = st.session_state.active_filters
        af = st.session_state.additional_filters
        
        for t in tickers:
            try:
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
                    price = day.get("c") or last.get("p") or minute_data.get("c") or prev.get("c") or 0
                    if price <= 0:
                        skipped_no_price += 1
                        continue
                    
                    open_price = day.get("o") or price
                    high = day.get("h") or price
                    low = day.get("l") or price
                    close = day.get("c") or price
                    vol = day.get("v") or minute_data.get("v") or 0
                    
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
                
                # GAP-Berechnung (Open vs Previous High/Low)
                gap_pct = 0
                day_open = day.get("o") or open_price
                if prev_high > 0 and prev_low > 0:
                    if day_open > prev_high:
                        gap_pct = ((day_open - prev_high) / prev_high) * 100
                    elif day_open < prev_low:
                        gap_pct = ((day_open - prev_low) / prev_low) * 100
                
                # WICK-Berechnungen
                candle_range = high - low if high > low else 0.0001
                body_top = max(open_price, close)
                body_bottom = min(open_price, close)
                
                upper_wick_pct = ((high - body_top) / candle_range) * 100 if candle_range > 0 else 0
                lower_wick_pct = ((body_bottom - low) / candle_range) * 100 if candle_range > 0 else 0
                
                # RVOL Berechnung - VERBESSERT mit Time-Normalisierung
                prev_vol = prev.get("v") or 0
                rvol = calculate_rvol_at_time(vol, prev_vol, session)
                rvol = min(rvol, 999.0)
                
                # Vortag Change
                prev_open = prev.get("o") or 0
                vortag_chg = round(((prev_close - prev_open) / prev_open) * 100, 2) if prev_open > 0 else 0
                
                close_pos = calculate_close_position(high, low, close)
                
                # ATR Berechnung (Volatilitäts-Kontext)
                atr_pct = calculate_atr_from_ohlc(high, low, close, prev_close)
                volatility_regime, vola_adj = get_volatility_regime(atr_pct)
                
                # Liquiditäts-Check (Gemini Fix: Keine Pennystocks mit 100 Aktien)
                is_liquid, dollar_volume = validate_liquidity(vol, price, min_dollar_volume=50000)
                
                # FILTER-LOGIK
                match = True
                
                # Liquiditäts-Filter für Gap-Strategien UND PM/AH Strategien (Gemini's Kritik)
                # Pre-Market ist dünn: ohne Dollar-Volume Filter zeigt Scanner illiquide Pennystocks
                current_strat = st.session_state.get("current_strategy", "")
                liquidity_strategies = [
                    "Gap Up", "Gap Down", "Gap Up (High Vol)", "Gap Down (High Vol)",
                    "PM Gainers 🌅", "PM Losers 🌅", "PM Gap & Go 🌅", "PM Penny Movers 🌅",
                    "AH Gainers 🌙", "AH Losers 🌙", "AH Earnings Movers 🌙"
                ]
                
                # PM/AH: Niedrigerer Threshold ($25k) weil weniger Volumen normal ist
                # Regular: Höherer Threshold ($50k)
                if current_strat in liquidity_strategies:
                    if session in ["Pre-Market", "After-Hours"]:
                        min_dollar_vol = 25000  # $25k für PM/AH
                    else:
                        min_dollar_vol = 50000  # $50k für Regular
                    
                    is_liquid, dollar_volume = validate_liquidity(vol, price, min_dollar_vol)
                    if not is_liquid:
                        skipped_filter += 1
                        continue  # Skip illiquide Trades
                
                if "RVOL" in f:
                    rvol_min, rvol_max = f["RVOL"]
                    if af.get("rvol_override_min"): rvol_min = af["rvol_override_min"]
                    if af.get("rvol_override_max"): rvol_max = af["rvol_override_max"]
                    if not (rvol_min <= rvol <= rvol_max): match = False
                
                if "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]): match = False
                if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]): match = False
                if "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]): match = False
                if "Close Position" in f and not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]): match = False
                
                # Neue Filter: Gap & Wicks
                if "Gap %" in f and not (f["Gap %"][0] <= gap_pct <= f["Gap %"][1]): match = False
                if "Upper Wick %" in f and not (f["Upper Wick %"][0] <= upper_wick_pct <= f["Upper Wick %"][1]): match = False
                if "Lower Wick %" in f and not (f["Lower Wick %"][0] <= lower_wick_pct <= f["Lower Wick %"][1]): match = False
                
                if af.get("preis_min", 0) > 0 and price < af["preis_min"]: match = False
                if af.get("preis_max", 100000) < 100000 and price > af["preis_max"]: match = False
                if af.get("nur_gewinner") and change <= 0: match = False
                if af.get("nur_verlierer") and change >= 0: match = False
                
                if not match:
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
                    "ClosePos": round(close_pos, 2), "Alpha": alpha,
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
            except:
                continue
        
        return results, skipped_no_price, skipped_filter
    except Exception as e:
        st.error(f"Polygon Fehler: {e}")
        return [], 0, 0

# =============================================================================
# INTERNATIONALE BÖRSEN - Top Aktien Listen
# =============================================================================
INTERNATIONAL_STOCKS = {
    "DE": {  # Deutschland XETRA
        "suffix": ".DE",
        "name": "Deutschland (XETRA)",
        "stocks": [
            "SAP", "SIE", "ALV", "DTE", "BAS", "BAYN", "MRK", "BMW", "VOW3", "MBG",
            "ADS", "IFX", "DB1", "MUV2", "HEN3", "DPW", "RWE", "EON", "FRE", "HEI",
            "CON", "BEI", "LIN", "FME", "VNA", "SRT3", "1COV", "MTX", "SY1", "PUM",
            "ZAL", "ENR", "HFG", "LEG", "AIR", "EVK", "DHER", "RHM", "SHL", "QIA"
        ]
    },
    "UK": {  # London Stock Exchange
        "suffix": ".L",
        "name": "UK (London)",
        "stocks": [
            "SHEL", "AZN", "HSBA", "ULVR", "BP", "GSK", "RIO", "DGE", "BATS", "REL",
            "LSEG", "NG", "VOD", "PRU", "LLOY", "BARC", "AAL", "BHP", "GLEN", "CRH",
            "RKT", "IMB", "SSE", "AHT", "ABF", "NWG", "EXPN", "SMT", "III", "WPP",
            "ANTO", "STAN", "LAND", "SGE", "PSON", "INF", "BA", "JD", "TSCO", "SBRY"
        ]
    },
    "CH": {  # Schweiz SIX
        "suffix": ".SW",
        "name": "Schweiz (SIX)",
        "stocks": [
            "NESN", "ROG", "NOVN", "UBSG", "ZURN", "ABBN", "CSGN", "SREN", "GIVN", "LONN",
            "SCMN", "SIKA", "GEBN", "PGHN", "CFR", "ALC", "SLHN", "BALN", "SGSN", "LOGN",
            "SOON", "TEMN", "VACN", "BARN", "HOLN", "SRENH", "STMN", "SCHP", "LISN", "SIGN",
            "MBTN", "EMMN", "DKSH", "BUCN", "SANN", "SFZN", "BCVN", "BEKN", "CERN", "TIBN"
        ]
    },
    "EU": {  # Euronext (Paris, Amsterdam)
        "suffix": ".PA",  # Hauptsächlich Paris
        "name": "Europa (Euronext)",
        "stocks": [
            # Frankreich
            "MC", "OR", "TTE", "SAN", "AIR", "SU", "BNP", "AI", "CS", "DG",
            "SAF", "RI", "KER", "BN", "VIV", "CA", "CAP", "EN", "GLE", "SGO",
            # Amsterdam (.AS)
            "ASML.AS", "INGA.AS", "PHIA.AS", "AD.AS", "HEIA.AS", "UNA.AS", "WKL.AS", "RAND.AS", "DSM.AS", "AKZA.AS"
        ]
    },
    "JP": {  # Tokyo Stock Exchange
        "suffix": ".T",
        "name": "Japan (Tokyo)",
        "stocks": [
            "7203", "6758", "9984", "8306", "6861", "6501", "7267", "9432", "8035", "4063",
            "6902", "7974", "8058", "9433", "4502", "6954", "8316", "7751", "3382", "6367",
            "8801", "4503", "6981", "7201", "9434", "4661", "7270", "6752", "8411", "7733",
            "5108", "8031", "4519", "6301", "8766", "9020", "4568", "2914", "8802", "6594"
        ]
    },
    "HK": {  # Hong Kong
        "suffix": ".HK",
        "name": "Hong Kong",
        "stocks": [
            "0700", "9988", "0005", "1299", "2318", "0939", "1398", "0388", "0941", "0883",
            "2628", "1211", "0027", "1038", "2382", "0011", "0016", "0001", "0066", "3988",
            "0267", "0669", "1928", "0175", "0002", "0012", "0003", "0688", "0386", "1113",
            "0823", "0006", "1997", "0019", "2269", "0960", "1109", "0762", "0017", "2020"
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

@st.cache_data(ttl=60)  # 1 Minute Cache
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
                
                resp = requests.get(url, params=params, headers=headers, timeout=10)
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
                
                # Filter-Logik
                match = True
                if "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]): match = False
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
                    "ClosePos": round(close_pos, 2),
                    "Alpha": alpha,
                    "Category": FUTURES_CONTRACTS[category]["name"],
                    "FullTicker": ticker,
                })
                
            except Exception:
                continue
        
        return results, skipped_no_price, skipped_filter
        
    except Exception as e:
        st.error(f"Futures Fehler: {e}")
        return [], 0, 0

@st.cache_data(ttl=60)  # 1 Minute Cache
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
    
    if category not in FOREX_PAIRS:
        return [], 0, 0
    
    pairs = FOREX_PAIRS[category]["pairs"]
    
    f = st.session_state.active_filters
    af = st.session_state.additional_filters
    
    try:
        for ticker, name in pairs:
            try:
                # Yahoo Finance API Query
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                params = {"interval": "1d", "range": "5d"}
                headers = {"User-Agent": "Mozilla/5.0"}
                
                resp = requests.get(url, params=params, headers=headers, timeout=10)
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
                
                # Filter-Logik
                match = True
                if "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]): match = False
                if af.get("nur_gewinner") and change <= 0: match = False
                if af.get("nur_verlierer") and change >= 0: match = False
                
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
                    "ClosePos": round(close_pos, 2),
                    "Alpha": round(alpha, 0),
                    "Category": FOREX_PAIRS[category]["name"],
                    "FullTicker": ticker,
                })
                
            except Exception:
                continue
        
        return results, skipped_no_price, skipped_filter
        
    except Exception as e:
        st.error(f"Forex Fehler: {e}")
        return [], 0, 0

@st.cache_data(ttl=60)  # 1 Minute Cache
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
    
    if exchange_code not in INTERNATIONAL_STOCKS:
        return [], 0, 0
    
    exchange = INTERNATIONAL_STOCKS[exchange_code]
    suffix = exchange["suffix"]
    stocks = exchange["stocks"]
    
    f = st.session_state.active_filters
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
                
                resp = requests.get(url, params=params, headers=headers, timeout=10)
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
                
                # RVOL
                rvol = round(today_vol / yesterday_vol, 2) if yesterday_vol > 0 else 1.0
                rvol = min(rvol, 999.0)
                
                # Close Position
                close_pos = calculate_close_position(today_high, today_low, price)
                
                # Wick Berechnungen
                candle_range = today_high - today_low if today_high > today_low else 0.0001
                body_top = max(today_open, today_close)
                body_bottom = min(today_open, today_close)
                upper_wick_pct = ((today_high - body_top) / candle_range) * 100
                lower_wick_pct = ((body_bottom - today_low) / candle_range) * 100
                
                # ATR
                atr_pct = calculate_atr_from_ohlc(today_high, today_low, today_close, yesterday_close)
                volatility_regime, _ = get_volatility_regime(atr_pct)
                
                # Dollar Volume
                dollar_volume = today_vol * price
                is_liquid = dollar_volume >= 50000
                
                # FILTER-LOGIK
                match = True
                
                if "RVOL" in f:
                    rvol_min, rvol_max = f["RVOL"]
                    if not (rvol_min <= rvol <= rvol_max): match = False
                
                if "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]): match = False
                if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]): match = False
                if "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]): match = False
                if "Close Position" in f and not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]): match = False
                if "Upper Wick %" in f and not (f["Upper Wick %"][0] <= upper_wick_pct <= f["Upper Wick %"][1]): match = False
                if "Lower Wick %" in f and not (f["Lower Wick %"][0] <= lower_wick_pct <= f["Lower Wick %"][1]): match = False
                
                if af.get("preis_min", 0) > 0 and price < af["preis_min"]: match = False
                if af.get("preis_max", 100000) < 100000 and price > af["preis_max"]: match = False
                if af.get("nur_gewinner") and change <= 0: match = False
                if af.get("nur_verlierer") and change >= 0: match = False
                
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
                    "ClosePos": round(close_pos, 2),
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
                
            except Exception:
                continue
        
        return results, skipped_no_price, skipped_filter
        
    except Exception as e:
        st.error(f"Yahoo Finance Fehler: {e}")
        return [], 0, 0

# =============================================================================
# 5. STREAMLIT UI
# =============================================================================
st.set_page_config(page_title="Alpha V61 Pro", layout="wide")

# AUTO-REFRESH (wenn aktiviert)
if st.session_state.auto_refresh_enabled:
    refresh_interval = st.session_state.get("refresh_interval", 5) * 60 * 1000  # in ms
    st_autorefresh(interval=refresh_interval, key="auto_refresh")

# -----------------------------------------------------------------------------
# SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("💎 Alpha V61 Pro")
    st.caption("Pre/Post Market | Insider | Gaps | AI")
    
    st.divider()
    
    # Markt-Auswahl (4 Kategorien)
    m_type = st.radio("📊 Markt:", ["Krypto", "Aktien", "Futures", "Forex"], horizontal=True)
    st.session_state.market_type = m_type
    
    if m_type == "Krypto":
        st.caption("📡 CoinGecko (Top 250) - 24/7")
    
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
            st.caption("📡 Polygon.io Premium - Near Realtime")
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
    
    st.divider()
    
    # Strategie-Auswahl - DYNAMISCH basierend auf Markt!
    st.subheader("🎯 Strategie")
    
    # Hole passende Strategien für aktuellen Markt
    current_strategies = get_strategies_for_market(m_type)
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
    
    # WICHTIG: Key muss zum Markt passen, sonst verwirrt Streamlit sich!
    strat = st.selectbox("Wähle Strategie:", strategy_list, index=default_index, key=f"strategy_select_{m_type}")
    
    # Strategie laden wenn sich Auswahl ändert
    if strat != st.session_state.get("current_strategy", ""):
        apply_strategy(strat, current_strategies)
        st.rerun()
    
    with st.expander("ℹ️ Info"):
        st.write(current_strategies[strat]["description"])
        st.caption(current_strategies[strat]['logic'])
        
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
                    step=0.05, key=f"slider_{filter_name}"
                )
                updated_filters[filter_name] = new_val
            elif filter_name == "Preis":
                new_val = st.slider(
                    f"{filter_name} ($)", 0.0, 10000.0, (float(values[0]), float(values[1])), 
                    key=f"slider_{filter_name}"
                )
                updated_filters[filter_name] = new_val
            else:
                min_v = -100.0 if "%" in filter_name else 0.0
                max_v = 100.0 if "%" in filter_name else 100.0
                new_val = st.slider(
                    filter_name, min_v, max_v, (float(values[0]), float(values[1])), 
                    key=f"slider_{filter_name}"
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
            
            st.session_state.additional_filters = {
                "preis_min": preis_min, "preis_max": preis_max,
                "nur_gewinner": nur_gewinner, "nur_verlierer": nur_verlierer,
                "rvol_override_min": None, "rvol_override_max": None,
            }
    
    st.divider()
    
    # SCAN Button
    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        # DEBUG: Zeige aktuelle Konfiguration
        st.caption(f"🔍 Debug: Markt={m_type}, Strategie={st.session_state.get('current_strategy', 'KEINE')}, Filter={st.session_state.active_filters}")
        
        # Prüfe ob Insider-Strategie gewählt
        current_strat = st.session_state.get("current_strategy", "")
        is_insider_strategy = current_strat in ["Insider Buying", "Insider Selling"]
        is_gap_strategy = current_strat in ["Gap Up", "Gap Down"]
        is_volume_void_strategy = current_strat in ["Volume Void Long 🕳️⬆️", "Volume Void Short 🕳️⬇️"]
        
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
                    candidates, _, _ = fetch_stock_data(poly_key, session="Regular")
                    
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
        
        elif not st.session_state.active_filters:
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
                    results, snp, sf = fetch_forex_data(forex_cat)
                    
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
                    results, snp, sf = fetch_stock_data(poly_key, session=session)
                    
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
                    
                    results, snp, sf = fetch_international_stock_data(exchange)
                    
                    if len(results) == 0:
                        st.warning(f"⚠️ Keine Ergebnisse für {exchange_name} mit aktuellen Filtern")
                
                st.session_state.scan_results = sorted(results, key=lambda x: x["Alpha"], reverse=True)[:50]
                
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
tab_scanner, tab_search, tab_watchlist, tab_moneyflow = st.tabs(["📊 Scanner", "🔍 Suche", "⭐ Watchlist", "💰 Money Flow"])

with tab_scanner:
    col_chart, col_journal = st.columns([2, 1])
    
    # Prüfe ob Insider-Strategie aktiv
    is_insider = st.session_state.current_strategy in ["Insider Buying", "Insider Selling"]
    is_volume_void = st.session_state.current_strategy in ["Volume Void Long 🕳️⬆️", "Volume Void Short 🕳️⬇️"]
    
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
            
            sel = st.dataframe(
                df[display_cols], on_select="rerun", selection_mode="single-row",
                hide_index=True, use_container_width=True,
                column_config=col_config
            )
            
            if sel.selection and sel.selection.rows:
                row = df.iloc[sel.selection.rows[0]]
                st.session_state.selected_symbol = str(row["Ticker"])
                st.session_state.current_data = row.to_dict()
                
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
                    except:
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
                    except:
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
                    except:
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
                        
                    except:
                        pass
                
                # Watchlist Button
                if st.button(f"⭐ {row['Ticker']} zur Watchlist", use_container_width=True):
                    if add_to_watchlist(row["Ticker"], row.to_dict()):
                        st.success(f"✅ {row['Ticker']} hinzugefügt!")
                    else:
                        st.info("Bereits in Watchlist")
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
                except:
                    pass
            
            # S/R mit historischen Daten berechnen
            (supports, resistances), fib_info = calculate_sr_levels(
                price=current_price,
                ticker=ticker,
                market_type=m_type,
                timeframe=selected_tf,
                poly_key=poly_key
            )
            st.session_state.sr_levels = {"support": supports, "resistance": resistances}
            st.session_state.fib_info = fib_info
        
        # S/R LEVELS ANZEIGE
        if st.session_state.sr_levels["support"] or st.session_state.sr_levels["resistance"]:
            st.caption(f"📐 Fibonacci S/R ({selected_tf})")
            col_s, col_r = st.columns(2)
            with col_s:
                st.markdown("**🟢 Support**")
                for i, s in enumerate(st.session_state.sr_levels["support"], 1):
                    st.caption(f"S{i}: ${s:,.4f}")
            with col_r:
                st.markdown("**🔴 Resistance**")
                for i, r in enumerate(st.session_state.sr_levels["resistance"], 1):
                    st.caption(f"R{i}: ${r:,.4f}")
            
            # Konsolidierungszonen anzeigen
            if st.session_state.get("fib_info", {}).get("consolidation_zones"):
                st.markdown("**🟣 Konsolidierungszonen** (High Activity)")
                for i, zone in enumerate(st.session_state.fib_info["consolidation_zones"], 1):
                    st.caption(f"Zone {i}: ${zone['low']:,.4f} - ${zone['high']:,.4f} ({zone['days']} Kerzen, {zone['pct_time']}%)")
            
            # Fibonacci Zusatz-Info anzeigen
            if st.session_state.get("fib_info"):
                with st.expander("📊 Fibonacci Details"):
                    fi = st.session_state.fib_info
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
                    m_type = st.session_state.market_type
                    
                    # Polygon Key für Aktien
                    poly_key = None
                    if m_type == "Aktien":
                        try:
                            poly_key = st.secrets["POLYGON_KEY"]
                        except:
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
            
            # TradingView Tipp
            st.info("💡 **Tipp:** Aktiviere im TradingView Chart den 'Volume Profile' Indikator für echte Volume-Daten")
        
        # TradingView Chart mit dynamischem Interval
        if st.session_state.market_type == "Krypto":
            tv_symbol = f"BINANCE:{st.session_state.selected_symbol}USDT"
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
                    search_resp = requests.get(search_url, timeout=15)
                    
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
                        markets_resp = requests.get(markets_url, params=params, timeout=30)
                        if markets_resp.status_code == 200:
                            for coin in markets_resp.json():
                                if coin.get("symbol", "").upper() == search_input:
                                    coin_id = coin.get("id")
                                    break
                    
                    # Jetzt Coin-Daten holen
                    if coin_id:
                        detail_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
                        params = {"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"}
                        detail_resp = requests.get(detail_url, params=params, timeout=15)
                        
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
                                "ClosePos": round(close_pos, 2),
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
                    resp = requests.get(url, params=params, timeout=15)
                    
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
                                    "ClosePos": round(close_pos, 2),
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
                    tv_symbol = search_result['Ticker']
                
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
                        news_resp = requests.get(
                            f"https://api.polygon.io/v2/reference/news?ticker={st.session_state.selected_symbol}&limit=3&apiKey={poly_key}",
                            timeout=10
                        ).json()
                        news_items = news_resp.get("results", [])
                        if news_items:
                            news_txt = "\n".join([f"- {n.get('title', 'N/A')}" for n in news_items])
                    except:
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
    st.subheader("💰 Money Flow Radar")
    st.caption("Wohin fließt das Smart Money? Sektor-Rotation & Asset-Klassen Analyse")
    
    # Refresh Button
    col_ref1, col_ref2 = st.columns([1, 4])
    with col_ref1:
        refresh_flow = st.button("🔄 Aktualisieren", key="refresh_moneyflow")
    
    # Cache für Money Flow Daten
    @st.cache_data(ttl=300)  # 5 Minuten Cache
    def fetch_sector_etfs(poly_key):
        """Holt Sektor-ETF Daten von Polygon"""
        sectors = {
            # US Sektor ETFs (SPDR)
            "XLK": {"name": "💻 Technology", "category": "Aktien"},
            "XLF": {"name": "🏦 Financials", "category": "Aktien"},
            "XLE": {"name": "⚡ Energy", "category": "Aktien"},
            "XLV": {"name": "🏥 Healthcare", "category": "Aktien"},
            "XLI": {"name": "🏭 Industrials", "category": "Aktien"},
            "XLY": {"name": "🛒 Consumer Disc.", "category": "Aktien"},
            "XLP": {"name": "🥫 Consumer Staples", "category": "Aktien"},
            "XLU": {"name": "💡 Utilities", "category": "Aktien"},
            "XLB": {"name": "�ite Materials", "category": "Aktien"},
            "XLRE": {"name": "🏠 Real Estate", "category": "Aktien"},
            "XLC": {"name": "📱 Communication", "category": "Aktien"},
            # Thematische ETFs
            "ITA": {"name": "🛡️ Defense/Aerospace", "category": "Themen"},
            "ARKK": {"name": "🚀 Innovation", "category": "Themen"},
            "SMH": {"name": "🔌 Semiconductors", "category": "Themen"},
            "TAN": {"name": "☀️ Solar Energy", "category": "Themen"},
            "HACK": {"name": "🔒 Cybersecurity", "category": "Themen"},
            "BOTZ": {"name": "🤖 AI & Robotics", "category": "Themen"},
            # Asset Klassen
            "GLD": {"name": "🥇 Gold", "category": "Commodities"},
            "SLV": {"name": "🥈 Silver", "category": "Commodities"},
            "USO": {"name": "🛢️ Oil", "category": "Commodities"},
            "UNG": {"name": "🔥 Natural Gas", "category": "Commodities"},
            # Bonds & Safe Haven
            "TLT": {"name": "📜 Long-Term Bonds", "category": "Bonds"},
            "SHY": {"name": "📄 Short-Term Bonds", "category": "Bonds"},
            "UUP": {"name": "💵 US Dollar", "category": "Currency"},
            # Indices
            "SPY": {"name": "📊 S&P 500", "category": "Index"},
            "QQQ": {"name": "📈 Nasdaq 100", "category": "Index"},
            "IWM": {"name": "📉 Russell 2000", "category": "Index"},
            "DIA": {"name": "🏛️ Dow Jones", "category": "Index"},
        }
        
        results = []
        try:
            for ticker, info in sectors.items():
                try:
                    url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey={poly_key}"
                    resp = requests.get(url, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        t = data.get("ticker", {})
                        day = t.get("day", {}) or {}
                        prev = t.get("prevDay", {}) or {}
                        
                        price = day.get("c") or 0
                        prev_close = prev.get("c") or 0
                        change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                        
                        vol = day.get("v") or 0
                        prev_vol = prev.get("v") or 1
                        rvol = vol / prev_vol if prev_vol > 0 else 1
                        
                        results.append({
                            "Ticker": ticker,
                            "Name": info["name"],
                            "Category": info["category"],
                            "Price": round(price, 2),
                            "Change%": round(change_pct, 2),
                            "RVOL": round(rvol, 2),
                            "Volume": vol
                        })
                except:
                    continue
        except:
            pass
        
        return results
    
    @st.cache_data(ttl=300)
    def fetch_crypto_categories():
        """Holt Krypto-Kategorien von CoinGecko"""
        categories = [
            {"id": "layer-1", "name": "🔗 Layer 1 (BTC, ETH, SOL)"},
            {"id": "layer-2", "name": "🔷 Layer 2 (ARB, OP, MATIC)"},
            {"id": "decentralized-finance-defi", "name": "🏛️ DeFi"},
            {"id": "artificial-intelligence", "name": "🤖 AI & Big Data"},
            {"id": "gaming", "name": "🎮 Gaming (GameFi)"},
            {"id": "meme-token", "name": "🐕 Meme Coins"},
            {"id": "exchange-based-tokens", "name": "🏦 Exchange Tokens"},
            {"id": "stablecoins", "name": "💵 Stablecoins"},
            {"id": "non-fungible-tokens-nft", "name": "🖼️ NFT & Metaverse"},
            {"id": "real-world-assets-rwa", "name": "🏠 Real World Assets"},
        ]
        
        results = []
        try:
            for cat in categories:
                try:
                    url = f"https://api.coingecko.com/api/v3/coins/markets"
                    params = {
                        "vs_currency": "usd",
                        "category": cat["id"],
                        "order": "market_cap_desc",
                        "per_page": 10,
                        "page": 1
                    }
                    resp = requests.get(url, params=params, timeout=10)
                    if resp.status_code == 200:
                        coins = resp.json()
                        if coins:
                            # Durchschnittliche Performance der Top 10 Coins
                            changes = [c.get("price_change_percentage_24h", 0) or 0 for c in coins]
                            avg_change = sum(changes) / len(changes) if changes else 0
                            total_vol = sum(c.get("total_volume", 0) or 0 for c in coins)
                            
                            results.append({
                                "Category": cat["name"],
                                "CategoryID": cat["id"],
                                "Change%": round(avg_change, 2),
                                "Volume24h": total_vol,
                                "TopCoins": ", ".join([c["symbol"].upper() for c in coins[:3]])
                            })
                except:
                    continue
                    
                # Rate limit für CoinGecko
                import time
                time.sleep(0.5)
        except:
            pass
        
        return results
    
    @st.cache_data(ttl=300)
    def fetch_market_indices(poly_key):
        """Holt Haupt-Markt-Indices"""
        indices = {
            "SPY": "S&P 500",
            "QQQ": "Nasdaq 100", 
            "IWM": "Russell 2000",
            "DIA": "Dow Jones",
            "VIX": "VIX (Fear Index)"
        }
        results = {}
        
        try:
            for ticker, name in indices.items():
                try:
                    # VIX braucht spezielle Behandlung
                    if ticker == "VIX":
                        url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/VIXY?apiKey={poly_key}"
                    else:
                        url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey={poly_key}"
                    
                    resp = requests.get(url, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        t = data.get("ticker", {})
                        day = t.get("day", {}) or {}
                        prev = t.get("prevDay", {}) or {}
                        
                        price = day.get("c") or 0
                        prev_close = prev.get("c") or 0
                        change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                        
                        results[ticker] = {
                            "name": name,
                            "price": price,
                            "change": round(change_pct, 2)
                        }
                except:
                    continue
        except:
            pass
        
        return results
    
    # Daten laden
    try:
        poly_key = st.secrets["POLYGON_KEY"]
        
        # Market Overview
        st.markdown("### 📊 Market Overview")
        indices = fetch_market_indices(poly_key)
        
        if indices:
            idx_cols = st.columns(len(indices))
            for i, (ticker, data) in enumerate(indices.items()):
                with idx_cols[i]:
                    delta_color = "normal" if data["change"] >= 0 else "inverse"
                    st.metric(
                        data["name"], 
                        f"${data['price']:,.2f}", 
                        f"{data['change']:+.2f}%",
                        delta_color=delta_color
                    )
        
        st.divider()
        
        # Zwei Spalten: Aktien Sektoren | Krypto Kategorien
        col_stocks, col_crypto = st.columns(2)
        
        with col_stocks:
            st.markdown("### 📈 Aktien Sektoren & Assets")
            
            sector_data = fetch_sector_etfs(poly_key)
            
            if sector_data:
                # Nach Kategorie gruppieren
                categories = {}
                for item in sector_data:
                    cat = item["Category"]
                    if cat not in categories:
                        categories[cat] = []
                    categories[cat].append(item)
                
                # Sortiere jede Kategorie nach Performance
                for cat_name, items in categories.items():
                    items_sorted = sorted(items, key=lambda x: x["Change%"], reverse=True)
                    
                    with st.expander(f"**{cat_name}**", expanded=(cat_name in ["Aktien", "Themen"])):
                        for item in items_sorted:
                            change = item["Change%"]
                            rvol = item["RVOL"]
                            
                            # Farbe basierend auf Performance
                            if change > 2:
                                color = "🟢"
                                bar = "█" * min(int(change), 15)
                            elif change > 0:
                                color = "🟡"
                                bar = "█" * min(int(change * 3), 10)
                            elif change > -2:
                                color = "🟠"
                                bar = "░" * min(int(abs(change) * 3), 10)
                            else:
                                color = "🔴"
                                bar = "░" * min(int(abs(change)), 15)
                            
                            # RVOL Indikator
                            rvol_icon = "🔥" if rvol > 1.5 else ""
                            
                            st.caption(f"{color} **{item['Name']}** ({item['Ticker']})")
                            st.caption(f"   {bar} {change:+.2f}% | RVOL: {rvol:.1f}x {rvol_icon}")
                
                # Top Movers
                st.markdown("#### 🚀 Top Gainers (Heute)")
                top_gainers = sorted(sector_data, key=lambda x: x["Change%"], reverse=True)[:5]
                for item in top_gainers:
                    st.caption(f"🟢 {item['Name']}: **{item['Change%']:+.2f}%**")
                
                st.markdown("#### 📉 Top Losers (Heute)")
                top_losers = sorted(sector_data, key=lambda x: x["Change%"])[:5]
                for item in top_losers:
                    st.caption(f"🔴 {item['Name']}: **{item['Change%']:+.2f}%**")
                
                # High Volume Alert
                st.markdown("#### 🔥 Unusual Volume (RVOL > 1.5)")
                high_vol = [x for x in sector_data if x["RVOL"] > 1.5]
                high_vol = sorted(high_vol, key=lambda x: x["RVOL"], reverse=True)[:5]
                if high_vol:
                    for item in high_vol:
                        st.caption(f"🔥 {item['Name']}: RVOL **{item['RVOL']:.1f}x** ({item['Change%']:+.2f}%)")
                else:
                    st.caption("Keine ungewöhnlichen Volumina heute")
            else:
                st.warning("Sektor-Daten konnten nicht geladen werden")
        
        with col_crypto:
            st.markdown("### 🪙 Krypto Kategorien")
            
            crypto_cats = fetch_crypto_categories()
            
            if crypto_cats:
                # Sortiere nach Performance
                crypto_sorted = sorted(crypto_cats, key=lambda x: x["Change%"], reverse=True)
                
                for item in crypto_sorted:
                    change = item["Change%"]
                    
                    # Farbe und Bar
                    if change > 5:
                        color = "🟢"
                        label = "HOT 🔥"
                    elif change > 2:
                        color = "🟢"
                        label = ""
                    elif change > 0:
                        color = "🟡"
                        label = ""
                    elif change > -2:
                        color = "🟠"
                        label = ""
                    else:
                        color = "🔴"
                        label = "WEAK"
                    
                    bar_len = min(int(abs(change)), 20)
                    bar = "█" * bar_len if change > 0 else "░" * bar_len
                    
                    st.caption(f"{color} **{item['Category']}** {label}")
                    st.caption(f"   {bar} {change:+.2f}%")
                    st.caption(f"   Top: {item['TopCoins']}")
                    st.divider()
                
                # Summary
                st.markdown("#### 📊 Krypto Sentiment")
                avg_change = sum(c["Change%"] for c in crypto_cats) / len(crypto_cats) if crypto_cats else 0
                positive = len([c for c in crypto_cats if c["Change%"] > 0])
                
                if avg_change > 3:
                    st.success(f"🚀 **BULLISH** - Markt-Durchschnitt: {avg_change:+.2f}%")
                elif avg_change > 0:
                    st.info(f"📈 **LEICHT POSITIV** - Durchschnitt: {avg_change:+.2f}%")
                elif avg_change > -3:
                    st.warning(f"📉 **LEICHT NEGATIV** - Durchschnitt: {avg_change:+.2f}%")
                else:
                    st.error(f"🔴 **BEARISH** - Durchschnitt: {avg_change:+.2f}%")
                
                st.caption(f"{positive}/{len(crypto_cats)} Kategorien im Plus")
            else:
                st.warning("Krypto-Kategorien konnten nicht geladen werden (CoinGecko Rate Limit?)")
        
        st.divider()
        
        # Money Flow Interpretation
        st.markdown("### 🧠 Money Flow Interpretation")
        
        # Analysiere die Daten
        if sector_data and crypto_cats:
            col_int1, col_int2 = st.columns(2)
            
            with col_int1:
                st.markdown("**Risk-On vs Risk-Off:**")
                
                # Risk-On Assets (Tech, Crypto, Small Caps)
                risk_on = [x for x in sector_data if x["Ticker"] in ["XLK", "QQQ", "IWM", "ARKK", "SMH"]]
                risk_on_avg = sum(x["Change%"] for x in risk_on) / len(risk_on) if risk_on else 0
                
                # Risk-Off Assets (Bonds, Gold, Utilities)
                risk_off = [x for x in sector_data if x["Ticker"] in ["TLT", "GLD", "XLU", "SHY"]]
                risk_off_avg = sum(x["Change%"] for x in risk_off) / len(risk_off) if risk_off else 0
                
                if risk_on_avg > risk_off_avg + 0.5:
                    st.success(f"🟢 **RISK-ON** - Geld fließt in Wachstum/Risiko")
                    st.caption(f"Risk-On: {risk_on_avg:+.2f}% vs Risk-Off: {risk_off_avg:+.2f}%")
                elif risk_off_avg > risk_on_avg + 0.5:
                    st.warning(f"🟡 **RISK-OFF** - Geld fließt in sichere Häfen")
                    st.caption(f"Risk-Off: {risk_off_avg:+.2f}% vs Risk-On: {risk_on_avg:+.2f}%")
                else:
                    st.info(f"⚖️ **NEUTRAL** - Kein klarer Trend")
            
            with col_int2:
                st.markdown("**Sektor Rotation:**")
                
                # Finde stärksten und schwächsten Sektor
                aktien_sektoren = [x for x in sector_data if x["Category"] == "Aktien"]
                if aktien_sektoren:
                    best = max(aktien_sektoren, key=lambda x: x["Change%"])
                    worst = min(aktien_sektoren, key=lambda x: x["Change%"])
                    
                    st.caption(f"🚀 Stärkster: **{best['Name']}** ({best['Change%']:+.2f}%)")
                    st.caption(f"📉 Schwächster: **{worst['Name']}** ({worst['Change%']:+.2f}%)")
                    
                    spread = best["Change%"] - worst["Change%"]
                    if spread > 3:
                        st.caption(f"⚠️ Hohe Sektor-Dispersion ({spread:.1f}%) - Stockpicking wichtig!")
                    else:
                        st.caption(f"✅ Niedrige Dispersion ({spread:.1f}%) - Breite Rally/Sell-Off")
        
        st.divider()
        st.caption("💡 **Tipp:** Daten werden alle 5 Minuten aktualisiert. Klicke 'Aktualisieren' für Live-Daten.")
        
    except KeyError:
        st.error("❌ POLYGON_KEY fehlt! Füge ihn in Settings → Secrets hinzu.")
    except Exception as e:
        st.error(f"Fehler beim Laden: {e}")

# -----------------------------------------------------------------------------
# FOOTER
# -----------------------------------------------------------------------------
st.divider()
c1, c2, c3 = st.columns(3)
with c1:
    st.caption("Alpha Station V60 Pro")
with c2:
    st.caption(f"Watchlist: {len(st.session_state.watchlist)} Ticker")
with c3:
    if st.session_state.auto_refresh_enabled:
        st.caption(f"🔄 Auto-Refresh: {st.session_state.refresh_interval} Min")
    else:
        st.caption("🔄 Auto-Refresh: Aus")
