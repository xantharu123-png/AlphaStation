import streamlit as st
import pandas as pd
import requests
import anthropic
import json
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
    st.session_state.current_strategy = None
if "market_type" not in st.session_state:
    st.session_state.market_type = "Krypto"
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
    # GAP STRATEGIEN - NUR AKTIEN!
    "Gap Up": {
        "description": "📈 NUR AKTIEN: Gap nach oben - Gaps werden oft gefüllt",
        "filters": {"Gap %": (2.0, 50.0)},
        "logic": "Open > Previous High = Gap Up, wird oft gefüllt (Short-Chance)",
        "stocks_only": True
    },
    "Gap Down": {
        "description": "📉 NUR AKTIEN: Gap nach unten - Gaps werden oft gefüllt",
        "filters": {"Gap %": (-50.0, -2.0)},
        "logic": "Open < Previous Low = Gap Down, wird oft gefüllt (Long-Chance)",
        "stocks_only": True
    },
    # WICK STRATEGIEN - BEIDE MÄRKTE
    "Long Wick Up": {
        "description": "Lange obere Wick = Verkaufsdruck, oft Reversal nach unten",
        "filters": {"Upper Wick %": (30.0, 100.0), "Change %": (-10.0, 5.0)},
        "logic": "Lange obere Wick zeigt Ablehnung höherer Preise = Short-Signal"
    },
    "Long Wick Down": {
        "description": "Lange untere Wick = Kaufdruck, oft Reversal nach oben",
        "filters": {"Lower Wick %": (30.0, 100.0), "Change %": (-5.0, 10.0)},
        "logic": "Lange untere Wick zeigt Ablehnung tieferer Preise = Long-Signal"
    },
    # INSIDER STRATEGIEN - NUR AKTIEN
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
}

# =============================================================================
# 3. HELPER FUNCTIONS
# =============================================================================
def apply_strategy(strategy_name):
    if strategy_name in STRATEGIES:
        st.session_state.active_filters = STRATEGIES[strategy_name]["filters"].copy()
        st.session_state.current_strategy = strategy_name
        st.session_state.additional_filters = {
            "preis_min": 0.0, "preis_max": 100000.0,
            "nur_gewinner": False, "nur_verlierer": False,
            "rvol_override_min": None, "rvol_override_max": None,
        }

def calculate_close_position(high, low, close):
    if high == low or high is None or low is None:
        return 0.5
    return (close - low) / (high - low)

def calculate_alpha_score(rvol, vortag_pct, change_pct):
    return round((rvol * 12) + (abs(vortag_pct) * 10) + (abs(change_pct) * 8), 2)

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
        return calculate_sr_levels_simple(current_price), {}
    
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
            # Hole 24h UND 7d change - daraus können wir Vortag approximieren
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
                # 7d change enthält die letzten 7 Tage inkl. heute
                # Vortag ≈ (7d_change - 24h_change) / 6 * Tage
                # Besser: Wir schätzen den Vortag aus der Differenz
                change_7d = coin.get("price_change_percentage_7d_in_currency") or 0
                
                # Approximation: Vortag = (Preis vor 48h -> Preis vor 24h)
                # Wenn wir 24h und 7d haben:
                # price_48h_ago = price / (1 + change_24h/100) / (1 + vortag/100)
                # Vereinfachte Schätzung: 
                if change_7d != 0 and change_24h != 0:
                    # Durchschnittliche tägliche Änderung der letzten 7 Tage (ohne heute)
                    avg_daily_7d = change_7d / 7
                    # Vortag ≈ avg * 1.5 (gewichtet auf recent)
                    vortag_chg = round(avg_daily_7d * 1.5, 2)
                    # Korrektur: Wenn heute stark anders als Durchschnitt, anpassen
                    if abs(change_24h) > abs(avg_daily_7d) * 3:
                        # Heute ist ein Ausreißer - Vortag war wahrscheinlich ruhiger
                        vortag_chg = round(avg_daily_7d, 2)
                else:
                    # Fallback: Keine 7d Daten, nutze 24h als grobe Schätzung
                    # ABER: Setze auf 0 wenn wir wirklich keine Info haben
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
                })
            except:
                continue
        
        return results, 0, skipped_filter
    except Exception as e:
        st.error(f"CoinGecko Fehler: {e}")
        return [], 0, 0


def fetch_stock_data(poly_key):
    results = []
    skipped_no_price = 0
    skipped_filter = 0
    
    try:
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
                
                price = day.get("c") or last.get("p") or minute_data.get("c") or prev.get("c") or 0
                if price <= 0:
                    skipped_no_price += 1
                    continue
                
                # OHLC Daten
                open_price = day.get("o") or price
                high = day.get("h") or price
                low = day.get("l") or price
                close = day.get("c") or price
                
                # Previous Day Daten für Gap-Berechnung
                prev_high = prev.get("h") or 0
                prev_low = prev.get("l") or 0
                prev_close = prev.get("c") or 0
                
                # GAP-Berechnung
                # Gap Up: Open > Previous High
                # Gap Down: Open < Previous Low
                gap_pct = 0
                if prev_high > 0 and prev_low > 0:
                    if open_price > prev_high:
                        # Gap Up: Wie viel % über dem Previous High
                        gap_pct = ((open_price - prev_high) / prev_high) * 100
                    elif open_price < prev_low:
                        # Gap Down: Wie viel % unter dem Previous Low (negativ)
                        gap_pct = ((open_price - prev_low) / prev_low) * 100
                
                # WICK-Berechnungen
                candle_range = high - low if high > low else 0.0001
                body_top = max(open_price, close)
                body_bottom = min(open_price, close)
                
                # Upper Wick %: (High - Body Top) / Candle Range * 100
                upper_wick_pct = ((high - body_top) / candle_range) * 100 if candle_range > 0 else 0
                
                # Lower Wick %: (Body Bottom - Low) / Candle Range * 100
                lower_wick_pct = ((body_bottom - low) / candle_range) * 100 if candle_range > 0 else 0
                
                change = t.get("todaysChangePerc")
                if change is None:
                    change = ((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0
                change = change or 0
                
                vol = day.get("v") or minute_data.get("v") or 0
                prev_vol = prev.get("v") or 0
                rvol = round(vol / prev_vol, 2) if prev_vol > 0 and vol > 0 else 1.0
                rvol = min(rvol, 999.0)
                
                prev_open = prev.get("o") or 0
                vortag_chg = round(((prev_close - prev_open) / prev_open) * 100, 2) if prev_open > 0 else 0
                
                close_pos = calculate_close_position(high, low, close)
                
                # FILTER-LOGIK
                match = True
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
                
                results.append({
                    "Ticker": ticker_raw, "Name": "",
                    "Preis": round(price, 4), "Chg%": round(change, 2),
                    "RVOL": rvol, "Vortag%": vortag_chg,
                    "ClosePos": round(close_pos, 2), "Alpha": alpha,
                    "Gap%": round(gap_pct, 2),
                    "UpperWick%": round(upper_wick_pct, 1),
                    "LowerWick%": round(lower_wick_pct, 1),
                })
            except:
                continue
        
        return results, skipped_no_price, skipped_filter
    except Exception as e:
        st.error(f"Polygon Fehler: {e}")
        return [], 0, 0

# =============================================================================
# 5. STREAMLIT UI
# =============================================================================
st.set_page_config(page_title="Alpha V52 Pro", layout="wide")

# AUTO-REFRESH (wenn aktiviert)
if st.session_state.auto_refresh_enabled:
    refresh_interval = st.session_state.get("refresh_interval", 5) * 60 * 1000  # in ms
    st_autorefresh(interval=refresh_interval, key="auto_refresh")

# -----------------------------------------------------------------------------
# SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("💎 Alpha V52 Pro")
    st.caption("Insider Trading | Gaps | Wicks | AI Reports")
    
    st.divider()
    
    # Markt-Auswahl
    m_type = st.radio("📊 Markt:", ["Krypto", "Aktien"], horizontal=True)
    st.session_state.market_type = m_type
    
    if m_type == "Krypto":
        st.caption("📡 CoinGecko (Top 250)")
    else:
        st.caption("📡 Polygon.io")
    
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
    
    # Strategie-Auswahl
    st.subheader("🎯 Strategie")
    strat = st.selectbox("Wähle Strategie:", list(STRATEGIES.keys()))
    
    with st.expander("ℹ️ Info"):
        st.write(STRATEGIES[strat]["description"])
        st.caption(STRATEGIES[strat]['logic'])
        
        # Warnungen für marktspezifische Strategien
        if strat in ["Gap Up", "Gap Down"]:
            st.warning("⚠️ Gap-Strategien funktionieren nur bei **Aktien**! Krypto handelt 24/7 und hat keine echten Gaps.")
        if strat in ["Insider Buying", "Insider Selling"]:
            st.warning("⚠️ Insider-Strategien funktionieren nur bei **Aktien**!")
        if strat in ["Bull Flag", "Bear Flag", "Reversal Hunter"]:
            st.info("ℹ️ Bei Krypto wird 'Vortag%' aus 7-Tage-Daten approximiert.")
    
    if st.button("📥 Strategie laden", use_container_width=True):
        apply_strategy(strat)
        st.rerun()
    
    st.divider()
    
    # Aktive Filter
    if st.session_state.active_filters:
        st.subheader("⚙️ Filter")
        
        for filter_name, values in list(st.session_state.active_filters.items()):
            # Überspringe Insider-Filter (kein Slider)
            if filter_name == "Insider":
                continue
                
            if filter_name == "Close Position":
                st.session_state.active_filters[filter_name] = st.slider(
                    f"{filter_name}", 0.0, 1.0, (float(values[0]), float(values[1])), 
                    step=0.05, key=f"b_{filter_name}"
                )
            elif filter_name == "Preis":
                st.session_state.active_filters[filter_name] = st.slider(
                    f"{filter_name} ($)", 0.0, 10000.0, (float(values[0]), float(values[1])), 
                    key=f"b_{filter_name}"
                )
            elif isinstance(values, tuple) and len(values) == 2:
                min_v = -100.0 if "%" in filter_name else 0.0
                max_v = 100.0 if "%" in filter_name else 100.0
                st.session_state.active_filters[filter_name] = st.slider(
                    filter_name, min_v, max_v, (float(values[0]), float(values[1])), 
                    key=f"b_{filter_name}"
                )
        
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
        # Prüfe ob Insider-Strategie gewählt
        current_strat = st.session_state.current_strategy
        is_insider_strategy = current_strat in ["Insider Buying", "Insider Selling"]
        is_gap_strategy = current_strat in ["Gap Up", "Gap Down"]
        
        # Warnung: Gap-Strategie bei Krypto
        if is_gap_strategy and m_type == "Krypto":
            st.error("❌ Gap-Strategien funktionieren nicht bei Krypto! Krypto handelt 24/7 und hat keine echten Gaps. Bitte wechsle zu **Aktien**.")
        
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
            with st.status(f"Scanne {m_type}...") as status:
                if m_type == "Krypto":
                    results, snp, sf = fetch_crypto_data()
                    
                    # Info wenn keine Ergebnisse und Gap-Filter aktiv
                    if len(results) == 0 and "Gap %" in st.session_state.active_filters:
                        st.warning("⚠️ Keine Ergebnisse - Gap-Filter bei Krypto findet nichts (keine Gaps bei 24/7 Handel)")
                else:
                    poly_key = st.secrets["POLYGON_KEY"]
                    results, snp, sf = fetch_stock_data(poly_key)
                
                st.session_state.scan_results = sorted(results, key=lambda x: x["Alpha"], reverse=True)[:50]
                status.update(label=f"✅ {len(st.session_state.scan_results)} Signale", state="complete")

# -----------------------------------------------------------------------------
# HAUPTBEREICH - TABS
# -----------------------------------------------------------------------------
tab_scanner, tab_search, tab_watchlist = st.tabs(["📊 Scanner", "🔍 Suche", "⭐ Watchlist"])

with tab_scanner:
    col_chart, col_journal = st.columns([2, 1])
    
    # Prüfe ob Insider-Strategie aktiv
    is_insider = st.session_state.current_strategy in ["Insider Buying", "Insider Selling"]
    
    with col_journal:
        st.subheader("📋 Ergebnisse")
        if st.session_state.current_strategy:
            st.caption(f"{st.session_state.current_strategy} | {st.session_state.market_type}")
        
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
                if is_insider and "Transactions" in row:
                    st.divider()
                    st.caption("📊 Letzte Transaktionen:")
                    for t in row.get("Transactions", [])[:3]:
                        emoji = "🟢" if t["type"] == "BUY" else "🔴"
                        st.caption(f"{emoji} {t['name'][:20]}: {t['shares']:,.0f} Aktien (${t['value']:,.0f})")
                
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
# FOOTER
# -----------------------------------------------------------------------------
st.divider()
c1, c2, c3 = st.columns(3)
with c1:
    st.caption("Alpha Station V52 Pro")
with c2:
    st.caption(f"Watchlist: {len(st.session_state.watchlist)} Ticker")
with c3:
    if st.session_state.auto_refresh_enabled:
        st.caption(f"🔄 Auto-Refresh: {st.session_state.refresh_interval} Min")
    else:
        st.caption("🔄 Auto-Refresh: Aus")
