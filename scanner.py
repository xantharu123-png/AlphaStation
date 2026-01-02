import streamlit as st
import pandas as pd
import requests
import anthropic
from datetime import datetime

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

# =============================================================================
# 2. STRATEGIE-DEFINITIONEN (Kritisch geprüft)
# =============================================================================
STRATEGIES = {
    "Volume Surge": {
        "description": "Aktien/Krypto mit überdurchschnittlichem Volumen",
        "filters": {
            "RVOL": (2.0, 50.0),
        },
        "logic": "RVOL > 2.0 zeigt erhöhtes Interesse"
    },
    "Bull Flag": {
        "description": "Konsolidierung nach starkem Anstieg - Volumen nimmt ab",
        "filters": {
            "Vortag %": (4.0, 25.0),
            "Change %": (-2.0, 2.0),
            "RVOL": (0.3, 1.5),
        },
        "logic": "Vortag stark positiv, heute seitwärts, Volumen sinkt = Bullflag"
    },
    "Bear Flag": {
        "description": "Konsolidierung nach Abverkauf - Short-Setup",
        "filters": {
            "Vortag %": (-25.0, -4.0),
            "Change %": (-2.0, 2.0),
            "RVOL": (0.3, 1.5),
        },
        "logic": "Vortag stark negativ, heute seitwärts, Volumen sinkt = Bearflag"
    },
    "Breakout Long": {
        "description": "Momentum-Ausbruch mit Volumen-Bestätigung",
        "filters": {
            "Change %": (5.0, 50.0),
            "RVOL": (2.0, 50.0),
            "Close Position": (0.75, 1.0),
        },
        "logic": "Starker Anstieg + hohes Volumen + Close nahe High"
    },
    "Breakdown Short": {
        "description": "Abverkauf mit Volumen - Short-Chance",
        "filters": {
            "Change %": (-50.0, -5.0),
            "RVOL": (2.0, 50.0),
            "Close Position": (0.0, 0.25),
        },
        "logic": "Starker Abverkauf + hohes Volumen + Close nahe Low"
    },
    "Penny Rockets": {
        "description": "Günstige Coins/Aktien mit explosivem Volumen",
        "filters": {
            "Preis": (0.0001, 1.0),
            "RVOL": (3.0, 100.0),
            "Change %": (2.0, 100.0),
        },
        "logic": "Lowcaps unter $1 mit extremem Interesse"
    },
    "Dip Buy": {
        "description": "Qualitäts-Assets im Rücksetzer ohne Panik",
        "filters": {
            "Preis": (10.0, 100000.0),
            "Change %": (-8.0, -2.0),
            "RVOL": (0.5, 2.0),
        },
        "logic": "Moderater Rücksetzer ohne Volumen-Panik = Kaufchance"
    },
    "Reversal Hunter": {
        "description": "Trendumkehr nach starkem Abverkauf",
        "filters": {
            "Vortag %": (-50.0, -5.0),
            "Change %": (2.0, 30.0),
            "RVOL": (1.5, 50.0),
        },
        "logic": "Gestern Crash, heute Käufer = potenzielle Umkehr"
    },
    "Early Momentum": {
        "description": "Starker Tagesstart mit Volumen",
        "filters": {
            "Change %": (3.0, 30.0),
            "RVOL": (1.5, 50.0),
        },
        "logic": "Positive Bewegung mit überdurchschnittlichem Volumen"
    },
    "Whale Watch": {
        "description": "Extremes Volumen - Big Player aktiv",
        "filters": {
            "RVOL": (5.0, 100.0),
        },
        "logic": "RVOL > 5.0 = institutionelles Interesse wahrscheinlich"
    },
}

# =============================================================================
# 3. HELPER FUNCTIONS
# =============================================================================
def apply_strategy(strategy_name):
    """Lädt die Basis-Filter einer Strategie"""
    if strategy_name in STRATEGIES:
        st.session_state.active_filters = STRATEGIES[strategy_name]["filters"].copy()
        st.session_state.current_strategy = strategy_name
        st.session_state.additional_filters = {
            "preis_min": 0.0,
            "preis_max": 100000.0,
            "nur_gewinner": False,
            "nur_verlierer": False,
            "rvol_override_min": None,
            "rvol_override_max": None,
        }

def calculate_close_position(high, low, close):
    """Berechnet wo der Close innerhalb der Tagesrange liegt (0=Low, 1=High)"""
    if high == low or high is None or low is None:
        return 0.5
    return (close - low) / (high - low)

def calculate_alpha_score(rvol, vortag_pct, change_pct):
    """Alpha = (RVOL * 12) + (|Vortag%| * 10) + (|Change%| * 8)"""
    return round((rvol * 12) + (abs(vortag_pct) * 10) + (abs(change_pct) * 8), 2)

# =============================================================================
# 4. DATA FETCHING FUNCTIONS
# =============================================================================
def fetch_crypto_data():
    """Holt Krypto-Daten von CoinGecko (kostenlos)"""
    results = []
    skipped_filter = 0
    
    try:
        # CoinGecko API - Top 250 Coins nach Marktkapitalisierung
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": 1,
            "sparkline": False,
            "price_change_percentage": "24h"
        }
        
        resp = requests.get(url, params=params, timeout=30)
        
        if resp.status_code == 429:
            st.warning("⚠️ CoinGecko Rate Limit erreicht. Warte 60 Sekunden und versuche erneut.")
            return [], 0, 0
        
        coins = resp.json()
        
        if not isinstance(coins, list):
            st.error(f"API Fehler: {coins}")
            return [], 0, 0
        
        f = st.session_state.active_filters
        af = st.session_state.additional_filters
        
        for coin in coins:
            try:
                price = coin.get("current_price") or 0
                if price <= 0:
                    continue
                
                # Metriken
                change_24h = coin.get("price_change_percentage_24h") or 0
                high_24h = coin.get("high_24h") or price
                low_24h = coin.get("low_24h") or price
                
                # Volumen-Ratio berechnen (aktuelles Vol / Durchschnitt)
                vol_24h = coin.get("total_volume") or 0
                market_cap = coin.get("market_cap") or 1
                
                # RVOL Approximation: Vol/MarketCap Ratio normalisiert
                # Höheres Volumen relativ zur Marktkapitalisierung = höherer RVOL
                if market_cap > 0:
                    vol_ratio = (vol_24h / market_cap) * 100  # Prozent
                    # Normalisieren auf RVOL-Skala (typisch 0.5-10)
                    rvol = round(vol_ratio * 5, 2)  # Skalierungsfaktor
                    rvol = max(0.1, min(rvol, 100))  # Clamp zwischen 0.1 und 100
                else:
                    rvol = 1.0
                
                # Vortag % - CoinGecko hat keine direkten Vortag-Daten
                # Wir nutzen die 24h Change als Proxy
                vortag_chg = change_24h  # Approximation
                
                # Close Position
                close_pos = calculate_close_position(high_24h, low_24h, price)
                
                # =================================================
                # FILTER-LOGIK
                # =================================================
                match = True
                
                # Basis-Filter aus Strategie
                if "RVOL" in f:
                    rvol_min, rvol_max = f["RVOL"]
                    if af.get("rvol_override_min") is not None:
                        rvol_min = af["rvol_override_min"]
                    if af.get("rvol_override_max") is not None:
                        rvol_max = af["rvol_override_max"]
                    if not (rvol_min <= rvol <= rvol_max):
                        match = False
                
                if "Change %" in f and not (f["Change %"][0] <= change_24h <= f["Change %"][1]):
                    match = False
                
                if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]):
                    match = False
                
                if "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]):
                    match = False
                
                if "Close Position" in f and not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]):
                    match = False
                
                # Zusatzfilter
                if af.get("preis_min", 0) > 0 and price < af["preis_min"]:
                    match = False
                if af.get("preis_max", 100000) < 100000 and price > af["preis_max"]:
                    match = False
                if af.get("nur_gewinner") and change_24h <= 0:
                    match = False
                if af.get("nur_verlierer") and change_24h >= 0:
                    match = False
                
                if not match:
                    skipped_filter += 1
                    continue
                
                # Ticker-Symbol (uppercase)
                ticker = coin.get("symbol", "").upper()
                
                alpha = calculate_alpha_score(rvol, vortag_chg, change_24h)
                
                results.append({
                    "Ticker": ticker,
                    "Name": coin.get("name", "")[:15],  # Gekürzt für Tabelle
                    "Preis": round(price, 6),
                    "Chg%": round(change_24h, 2),
                    "RVOL": rvol,
                    "Vortag%": round(vortag_chg, 2),
                    "ClosePos": round(close_pos, 2),
                    "Alpha": alpha,
                })
                
            except Exception:
                continue
        
        return results, 0, skipped_filter
        
    except Exception as e:
        st.error(f"CoinGecko API Fehler: {e}")
        return [], 0, 0


def fetch_stock_data(poly_key):
    """Holt Aktien-Daten von Polygon.io"""
    results = []
    skipped_no_price = 0
    skipped_filter = 0
    
    try:
        url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
        resp = requests.get(url, timeout=30).json()
        tickers = resp.get("tickers", [])
        
        if len(tickers) == 0:
            api_status = resp.get("status", "unknown")
            st.warning(f"API Status: {api_status} - Keine Ticker erhalten.")
            return [], 0, 0
        
        f = st.session_state.active_filters
        af = st.session_state.additional_filters
        
        for t in tickers:
            try:
                day = t.get("day", {}) or {}
                prev = t.get("prevDay", {}) or {}
                last = t.get("lastTrade", {}) or {}
                minute_data = t.get("min", {}) or {}
                
                # Preis ermitteln
                price = (
                    day.get("c") or 
                    last.get("p") or 
                    minute_data.get("c") or 
                    prev.get("c") or 
                    0
                )
                if price <= 0:
                    skipped_no_price += 1
                    continue
                
                # Metriken
                high = day.get("h") or price
                low = day.get("l") or price
                close = day.get("c") or price
                
                change = t.get("todaysChangePerc")
                if change is None:
                    prev_close_price = prev.get("c") or price
                    if prev_close_price > 0:
                        change = ((price - prev_close_price) / prev_close_price) * 100
                    else:
                        change = 0
                change = change or 0
                
                # RVOL
                vol = day.get("v") or minute_data.get("v") or 0
                prev_vol = prev.get("v") or 0
                if prev_vol > 0 and vol > 0:
                    rvol = round(vol / prev_vol, 2)
                else:
                    rvol = 1.0
                rvol = min(rvol, 999.0)
                
                # Vortag Change
                prev_open = prev.get("o") or 0
                prev_close = prev.get("c") or 0
                if prev_open > 0:
                    vortag_chg = round(((prev_close - prev_open) / prev_open) * 100, 2)
                else:
                    vortag_chg = 0
                
                close_pos = calculate_close_position(high, low, close)
                
                # =================================================
                # FILTER-LOGIK
                # =================================================
                match = True
                
                if "RVOL" in f:
                    rvol_min, rvol_max = f["RVOL"]
                    if af.get("rvol_override_min") is not None:
                        rvol_min = af["rvol_override_min"]
                    if af.get("rvol_override_max") is not None:
                        rvol_max = af["rvol_override_max"]
                    if not (rvol_min <= rvol <= rvol_max):
                        match = False
                
                if "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]):
                    match = False
                
                if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]):
                    match = False
                
                if "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]):
                    match = False
                
                if "Close Position" in f and not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]):
                    match = False
                
                # Zusatzfilter
                if af.get("preis_min", 0) > 0 and price < af["preis_min"]:
                    match = False
                if af.get("preis_max", 100000) < 100000 and price > af["preis_max"]:
                    match = False
                if af.get("nur_gewinner") and change <= 0:
                    match = False
                if af.get("nur_verlierer") and change >= 0:
                    match = False
                
                if not match:
                    skipped_filter += 1
                    continue
                
                ticker_raw = t.get("ticker", "")
                alpha = calculate_alpha_score(rvol, vortag_chg, change)
                
                results.append({
                    "Ticker": ticker_raw,
                    "Name": "",
                    "Preis": round(price, 4),
                    "Chg%": round(change, 2),
                    "RVOL": rvol,
                    "Vortag%": vortag_chg,
                    "ClosePos": round(close_pos, 2),
                    "Alpha": alpha,
                })
                
            except Exception:
                continue
        
        return results, skipped_no_price, skipped_filter
        
    except Exception as e:
        st.error(f"Polygon API Fehler: {e}")
        return [], 0, 0

# =============================================================================
# 5. STREAMLIT UI
# =============================================================================
st.set_page_config(page_title="Alpha V48 Pro", layout="wide")

# -----------------------------------------------------------------------------
# SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("💎 Alpha V48 Pro")
    st.caption("Krypto: CoinGecko | Aktien: Polygon")
    
    st.divider()
    
    # Markt-Auswahl
    m_type = st.radio("📊 Markt:", ["Krypto", "Aktien"], horizontal=True)
    st.session_state.market_type = m_type
    
    # Info zur Datenquelle
    if m_type == "Krypto":
        st.caption("📡 Datenquelle: CoinGecko (Top 250 Coins)")
    else:
        st.caption("📡 Datenquelle: Polygon.io")
    
    st.divider()
    
    # Strategie-Auswahl
    st.subheader("🎯 Strategie")
    strat = st.selectbox(
        "Wähle Strategie:",
        list(STRATEGIES.keys()),
        help="Jede Strategie hat vordefinierte Filter"
    )
    
    with st.expander("ℹ️ Strategie-Info"):
        st.write(f"**{strat}**")
        st.write(STRATEGIES[strat]["description"])
        st.caption(f"Logik: {STRATEGIES[strat]['logic']}")
    
    if st.button("📥 Strategie laden", use_container_width=True):
        apply_strategy(strat)
        st.rerun()
    
    st.divider()
    
    # Aktive Filter
    if st.session_state.active_filters:
        st.subheader("⚙️ Basis-Filter")
        st.caption(f"Strategie: {st.session_state.current_strategy}")
        
        for filter_name, values in list(st.session_state.active_filters.items()):
            if filter_name == "Close Position":
                st.session_state.active_filters[filter_name] = st.slider(
                    f"{filter_name} (0=Low, 1=High)",
                    0.0, 1.0,
                    (float(values[0]), float(values[1])),
                    step=0.05,
                    key=f"base_{filter_name}"
                )
            elif filter_name == "Preis":
                st.session_state.active_filters[filter_name] = st.slider(
                    f"{filter_name} ($)",
                    0.0, 10000.0,
                    (float(values[0]), float(values[1])),
                    key=f"base_{filter_name}"
                )
            else:
                min_val = -100.0 if "%" in filter_name else 0.0
                max_val = 100.0 if "%" in filter_name else 100.0
                st.session_state.active_filters[filter_name] = st.slider(
                    f"{filter_name}",
                    min_val, max_val,
                    (float(values[0]), float(values[1])),
                    key=f"base_{filter_name}"
                )
        
        st.divider()
        
        # Zusatzfilter
        st.subheader("🔧 Zusatzfilter")
        
        col1, col2 = st.columns(2)
        with col1:
            preis_min = st.number_input("Preis Min ($)", 0.0, 100000.0, 0.0, key="add_preis_min")
        with col2:
            preis_max = st.number_input("Preis Max ($)", 0.0, 100000.0, 100000.0, key="add_preis_max")
        
        col3, col4 = st.columns(2)
        with col3:
            nur_gewinner = st.checkbox("✅ Nur Gewinner", key="add_gewinner")
        with col4:
            nur_verlierer = st.checkbox("🔻 Nur Verlierer", key="add_verlierer")
        
        with st.expander("RVOL Override"):
            rvol_override = st.checkbox("RVOL manuell überschreiben")
            if rvol_override:
                rvol_min_override = st.number_input("RVOL Min", 0.1, 100.0, 1.0)
                rvol_max_override = st.number_input("RVOL Max", 0.1, 100.0, 50.0)
            else:
                rvol_min_override = None
                rvol_max_override = None
        
        st.session_state.additional_filters = {
            "preis_min": preis_min,
            "preis_max": preis_max,
            "nur_gewinner": nur_gewinner,
            "nur_verlierer": nur_verlierer,
            "rvol_override_min": rvol_min_override if rvol_override else None,
            "rvol_override_max": rvol_max_override if rvol_override else None,
        }
    
    st.divider()
    
    # SCAN Button
    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        if not st.session_state.active_filters:
            st.warning("Bitte zuerst eine Strategie laden!")
        else:
            with st.status(f"Scanne {m_type}...") as status:
                if m_type == "Krypto":
                    status.update(label="Hole Daten von CoinGecko...")
                    results, skipped_no_price, skipped_filter = fetch_crypto_data()
                else:
                    status.update(label="Hole Daten von Polygon.io...")
                    poly_key = st.secrets["POLYGON_KEY"]
                    results, skipped_no_price, skipped_filter = fetch_stock_data(poly_key)
                
                # Sortieren nach Alpha
                st.session_state.scan_results = sorted(results, key=lambda x: x["Alpha"], reverse=True)[:50]
                
                # Status-Meldung
                debug_msg = f"✅ {len(st.session_state.scan_results)} Signale gefunden"
                if skipped_no_price > 0:
                    debug_msg += f" | {skipped_no_price} ohne Preis"
                if skipped_filter > 0:
                    debug_msg += f" | {skipped_filter} gefiltert"
                status.update(label=debug_msg, state="complete")

# -----------------------------------------------------------------------------
# HAUPTBEREICH
# -----------------------------------------------------------------------------
col_chart, col_journal = st.columns([2, 1])

with col_journal:
    st.subheader("📋 Scan-Ergebnisse")
    if st.session_state.current_strategy:
        st.caption(f"Strategie: {st.session_state.current_strategy} | Markt: {st.session_state.market_type}")
    
    if st.session_state.scan_results:
        df = pd.DataFrame(st.session_state.scan_results)
        
        # Spalten für Anzeige (Name nur bei Krypto)
        display_cols = ["Ticker", "Preis", "Chg%", "RVOL", "Alpha"]
        if st.session_state.market_type == "Krypto" and "Name" in df.columns:
            display_cols = ["Ticker", "Name", "Preis", "Chg%", "RVOL", "Alpha"]
        
        sel = st.dataframe(
            df[display_cols],
            on_select="rerun",
            selection_mode="single-row",
            hide_index=True,
            use_container_width=True,
            column_config={
                "Preis": st.column_config.NumberColumn("Preis", format="$%.4f"),
                "Chg%": st.column_config.NumberColumn("Chg%", format="%.2f%%"),
                "RVOL": st.column_config.NumberColumn("RVOL", format="%.2fx"),
                "Alpha": st.column_config.NumberColumn("Alpha", format="%.1f ⭐"),
            }
        )
        
        if sel.selection and sel.selection.rows:
            row = df.iloc[sel.selection.rows[0]]
            st.session_state.selected_symbol = str(row["Ticker"])
            st.session_state.current_data = row.to_dict()
    else:
        st.info("Klicke auf 'SCAN STARTEN' um Signale zu finden")

with col_chart:
    st.subheader(f"📊 {st.session_state.selected_symbol}")
    
    # TradingView Chart
    if st.session_state.market_type == "Krypto":
        tv_symbol = f"BINANCE:{st.session_state.selected_symbol}USDT"
    else:
        tv_symbol = st.session_state.selected_symbol
    
    tv_html = f'''
    <div style="height:500px; border-radius: 8px; overflow: hidden;">
        <div id="tv_chart" style="height:100%"></div>
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
                "toolbar_bg": "#1a1a2e",
                "enable_publishing": false,
                "hide_side_toolbar": false,
                "allow_symbol_change": true,
                "studies": ["Volume@tv-basicstudies"],
                "container_id": "tv_chart"
            }});
        </script>
    </div>
    '''
    st.components.v1.html(tv_html, height=500)

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
        st.warning("Wähle zuerst einen Ticker aus der Liste!")
    else:
        with st.spinner("Claude analysiert..."):
            try:
                d = st.session_state.current_data
                m_type = st.session_state.market_type
                
                # News holen (nur für Aktien via Polygon)
                news_txt = "Keine News verfügbar."
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
                
                # Claude API Call
                client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                
                asset_name = d.get('Name', d['Ticker']) if m_type == "Krypto" else d['Ticker']
                
                prompt = f"""TECHNISCHES TERMINAL - ANALYSE

TICKER: {d['Ticker']}
NAME: {asset_name}
STRATEGIE: {st.session_state.current_strategy}
MARKT: {m_type}

DATEN:
- Preis: ${d['Preis']}
- 24h Änderung: {d['Chg%']}%
- RVOL (Volumen-Ratio): {d['RVOL']}x
- Close Position: {d.get('ClosePos', 0.5)} (0=Tagestief, 1=Tageshoch)
- Alpha-Score: {d['Alpha']}

NEWS:
{news_txt}

AUFGABEN:
1. Bewerte das Setup im Kontext der Strategie "{st.session_state.current_strategy}"
2. Nenne 3 konkrete Support-Level (basierend auf Preis und runden Zahlen)
3. Nenne 3 konkrete Resistance-Level
4. Berechne Risk/Reward Ratio für einen Trade
5. Gib ein Rating 1-100 für die Trade-Qualität
6. Klare Empfehlung: LONG / SHORT / ABWARTEN

Keine Disclaimers. Nur Fakten und Zahlen."""

                message = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1500,
                    system="Du bist ein präzises Finanz-Terminal für professionelle Trader. Antworte direkt, technisch und ohne Höflichkeitsfloskeln. Die gelieferten Daten sind Fakten - keine Ausreden.",
                    messages=[{"role": "user", "content": prompt}]
                )
                
                st.markdown(f"### 📊 Report: {d['Ticker']}")
                st.caption(f"Strategie: {st.session_state.current_strategy} | Alpha: {d['Alpha']}")
                st.divider()
                st.write(message.content[0].text)
                
            except Exception as e:
                st.error(f"Fehler: {e}")

# -----------------------------------------------------------------------------
# FOOTER
# -----------------------------------------------------------------------------
st.divider()
col_f1, col_f2 = st.columns(2)
with col_f1:
    st.caption("Alpha Station V48 Pro | Made for Miroslav")
with col_f2:
    st.caption("Krypto: CoinGecko API | Aktien: Polygon.io")
