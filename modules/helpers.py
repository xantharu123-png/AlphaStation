"""
Helpers Module — Extrahiert aus scanner.py (V70.4)

Utility-Funktionen: Trading Session, Volatility, SR-Levels, Heatmap etc.
"""
import os
import json
import datetime as dt
from datetime import datetime, timedelta
try:
    import pytz
except ImportError:
    pytz = None
from modules.data_fetchers import (
    fetch_historical_data_crypto, _fetch_historical_yahoo,
    fetch_historical_data_stocks
)


def _watchlist_file():
    """Pfad zur Watchlist-Datei."""
    return "/tmp/alpha_station_watchlist.json"


# ── Sector Mapping Dicts (für _resolve_sector_etf) ──
_SIC_TO_SECTOR = {
    "7371": "XLK", "7372": "XLK", "7373": "XLK", "7374": "XLK", "7375": "XLK", "7376": "XLK", "7377": "XLK", "7378": "XLK", "7379": "XLK",
    "3559": "XLK", "3669": "XLK", "3672": "XLK", "3674": "XLK", "3679": "XLK",
    "3577": "XLK", "3661": "XLK", "3663": "XLK", "3678": "XLK",
    "28": "XLV", "80": "XLV",
    "2830": "XLV", "2833": "XLV", "2834": "XLV", "2835": "XLV", "2836": "XLV",
    "3841": "XLV", "3842": "XLV", "3843": "XLV", "3844": "XLV", "3845": "XLV", "3851": "XLV",
    "5912": "XLV", "8000": "XLV", "8011": "XLV", "8049": "XLV", "8050": "XLV", "8060": "XLV", "8071": "XLV", "8082": "XLV", "8090": "XLV",
    "60": "XLF", "61": "XLF", "62": "XLF", "63": "XLF", "64": "XLF", "67": "XLF",
    "6020": "XLF", "6021": "XLF", "6022": "XLF", "6035": "XLF", "6036": "XLF",
    "6141": "XLF", "6153": "XLF", "6159": "XLF", "6162": "XLF", "6163": "XLF",
    "6199": "XLF", "6200": "XLF", "6211": "XLF", "6282": "XLF", "6311": "XLF", "6321": "XLF", "6324": "XLF", "6331": "XLF", "6399": "XLF",
    "13": "XLE", "29": "XLE", "1311": "XLE", "1381": "XLE", "1382": "XLE", "1389": "XLE", "2911": "XLE", "2990": "XLE",
    "25": "XLY", "53": "XLY", "54": "XLY", "55": "XLY", "56": "XLY", "57": "XLY", "58": "XLY", "59": "XLY", "70": "XLY", "72": "XLY", "78": "XLY", "79": "XLY",
    "5311": "XLY", "5411": "XLY", "5812": "XLY", "5944": "XLY", "5945": "XLY", "5961": "XLY", "7011": "XLY",
    "20": "XLP", "21": "XLP",
    "2000": "XLP", "2011": "XLP", "2013": "XLP", "2020": "XLP", "2030": "XLP", "2040": "XLP", "2050": "XLP", "2060": "XLP", "2080": "XLP", "2086": "XLP", "2090": "XLP",
    "2100": "XLP", "2111": "XLP",
    "15": "XLI", "16": "XLI", "17": "XLI", "34": "XLI", "40": "XLI", "42": "XLI", "44": "XLI", "45": "XLI",
    "3714": "XLI", "3720": "XLI", "3721": "XLI", "3724": "XLI", "3728": "XLI", "3743": "XLI",
    "4011": "XLI", "4013": "XLI", "4210": "XLI", "4213": "XLI", "4412": "XLI", "4512": "XLI", "4522": "XLI", "4581": "XLI",
    "10": "XLB", "12": "XLB", "14": "XLB", "24": "XLB", "26": "XLB", "30": "XLB", "32": "XLB", "33": "XLB",
    "49": "XLU", "4911": "XLU", "4922": "XLU", "4923": "XLU", "4924": "XLU", "4931": "XLU", "4932": "XLU", "4941": "XLU",
    "65": "XLRE", "6500": "XLRE", "6510": "XLRE", "6512": "XLRE", "6552": "XLRE", "6798": "XLRE",
    "27": "XLC", "48": "XLC", "4812": "XLC", "4813": "XLC", "4822": "XLC", "4833": "XLC", "4841": "XLC", "4899": "XLC",
    "7311": "XLC", "7812": "XLC", "7819": "XLC", "7822": "XLC",
}

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
            return "Regular", " Wochenende - zeige Freitag-Daten"
        
        # Session bestimmen
        if 4.0 <= current_time < 9.5:
            return "Pre-Market", f" Pre-Market ({now_et.strftime('%H:%M')} ET)"
        elif 9.5 <= current_time < 16.0:
            return "Regular", f"[+] Regular Hours ({now_et.strftime('%H:%M')} ET)"
        elif 16.0 <= current_time < 20.0:
            return "After-Hours", f" After-Hours ({now_et.strftime('%H:%M')} ET)"
        else:
            # Nachts → Nutze Regular (letzte Tagesdaten)
            return "Regular", f" Markt geschlossen ({now_et.strftime('%H:%M')} ET) - zeige letzte Daten"
            
    except Exception as e:
        # Fallback wenn pytz nicht funktioniert
        return "Regular", " Regular Hours"


def get_volatility_regime(atr_pct):
    """
    Klassifiziert das aktuelle Volatilitäts-Regime
    
    Returns: (regime_name, filter_adjustment)
    """
    if atr_pct < 1.5:
        return "LOW", 0.7
    elif atr_pct < 3.0:
        return "NORMAL", 1.0
    elif atr_pct < 5.0:
        return "HIGH", 1.3
    else:
        return "EXTREME", 1.5


SPAC_PATTERNS = [
    "ACQUISITION CORP", "ACQUISITION CO", "ACQUISITION INC",
    "BLANK CHECK", "SHELL COMPANY",
    "MERGER CORP", "MERGER SUB", "MERGER CO",
    "CAPITAL ACQUISITION", "HOLDINGS ACQUISITION",
    "SPAC ", " SPAC", "CLASS A ORDINARY SHARE",
    "SPECIAL PURPOSE", "SPONSOR",
    "CAPITAL CORP",          # Churchill Capital Corp VII
    "HEDOSOPHIA",            # Social Capital Hedosophia
    "REINVENT TECHNOLOGY",   # Reinvent Technology Partners
    "REPLAY ACQUISITION",
]


def is_spac(name):
    """Prüft ob ein Firmenname auf einen SPAC hindeutet."""
    if not name:
        return False
    name_upper = name.upper()
    return any(pattern in name_upper for pattern in SPAC_PATTERNS)


def _load_watchlist():
    """Lädt Watchlist aus JSON falls vorhanden."""
    try:
        import json
        with open(_watchlist_file(), "r") as f:
            return json.load(f)
    except Exception as e:
        return []


def format_vi_for_display(vi_result, current_price):
    """
    Formatiert Volume Imbalance Ergebnisse für die Scanner-Anzeige.
    
    Returns:
        dict mit Display-Feldern für das Scanner-UI
    """
    nb = vi_result["nearest_bull"]
    nbr = vi_result["nearest_bear"]
    stats = vi_result["stats"]
    
    display = {
        "VI_Total": stats["total"],
        "VI_Unfilled": stats["unfilled"],
        "VI_FillRate": stats["fill_rate"],
    }
    
    if nb:
        dist_pct = round((current_price - nb["zone_high"]) / current_price * 100, 2)
        display["Bull_VI"] = f"${nb['zone_low']:.2f}-${nb['zone_high']:.2f}"
        display["Bull_VI_Dist"] = f"{dist_pct:.1f}%"
        display["Bull_VI_Type"] = nb["type"]
        display["Bull_VI_Str"] = nb["strength"]
    
    if nbr:
        dist_pct = round((nbr["zone_low"] - current_price) / current_price * 100, 2)
        display["Bear_VI"] = f"${nbr['zone_low']:.2f}-${nbr['zone_high']:.2f}"
        display["Bear_VI_Dist"] = f"{dist_pct:.1f}%"
        display["Bear_VI_Type"] = nbr["type"]
        display["Bear_VI_Str"] = nbr["strength"]
    
    return display


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


def combined_score(level, current_price=0):
    """Score = Stärke * Proximity. current_price muss übergeben werden."""
    if current_price <= 0:
        return level.get("strength", 0)
    distance = abs(level["price"] - current_price) / current_price
    proximity_factor = max(0.3, 1.0 - distance * 2)
    return level["strength"] * proximity_factor


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
        # Lazy import to avoid circular dependency (analysis → helpers → analysis)
        from modules.analysis import calculate_sr_from_historical
        return calculate_sr_from_historical(ohlc_data, price)
    else:
        return calculate_sr_levels_simple(price)


def _crypto_breakout_ok(bar, range_high, range_low, atr, direction, recent_bars):
    """Crypto Breakout Confirmation OHNE Volume — nutzt Spread + Wick + Momentum."""
    if not bar or atr <= 0:
        return False

    # 1. Price Breakout: Close muss über/unter Range + ATR-Threshold
    if direction == "long":
        if bar["close"] <= range_high + atr * 0.2:
            return False
    else:
        if bar["close"] >= range_low - atr * 0.2:
            return False

    # 2. Spread-Expansion als Volume-Proxy
    bar_spread = bar["high"] - bar["low"]
    if recent_bars and len(recent_bars) >= 3:
        avg_spread = sum((b["high"] - b["low"]) for b in recent_bars[-5:]) / min(5, len(recent_bars))
        if avg_spread > 0 and bar_spread < avg_spread * 1.2:
            return False  # Kein Spread-Expansion → schwacher Breakout

    # 3. Wick-Check: Kein starkes Rejection
    bar_range = bar["high"] - bar["low"]
    if bar_range > 0:
        if direction == "long":
            upper_wick = bar["high"] - max(bar["open"], bar["close"])
            if (upper_wick / bar_range) > 0.25:
                return False  # >25% Upper Wick = Rejection
        else:
            lower_wick = min(bar["open"], bar["close"]) - bar["low"]
            if (lower_wick / bar_range) > 0.25:
                return False

    return True


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


def _pick_top_strikes(contracts, n=3, current_price=0):
    """Wählt die n nächsten Strikes zum current_price."""
    sorted_c = sorted(contracts, key=lambda c: abs(c.get("strike_price", 0) - current_price))
    return sorted_c[:n]


def _resolve_sector_etf(ticker, sic_code=""):
    """Mappt Ticker auf Sektor-ETF. Prüft 1) Override, 2) SIC-Code, 3) None."""
    # 1. Bekannter Override
    t = ticker.upper().split(".")[0]  # z.B. VNA.DE → VNA
    if t in _TICKER_SECTOR_OVERRIDE:
        return _TICKER_SECTOR_OVERRIDE[t]
    # 2. SIC-Code Matching (4-stellig → 2-stellig Fallback)
    sic = str(sic_code).strip()
    if sic and sic in _SIC_TO_SECTOR:
        return _SIC_TO_SECTOR[sic]
    if len(sic) >= 2 and sic[:2] in _SIC_TO_SECTOR:
        return _SIC_TO_SECTOR[sic[:2]]
    return None


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


