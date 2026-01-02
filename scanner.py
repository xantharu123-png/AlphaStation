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
    # Prüfen ob schon vorhanden
    existing = [w["ticker"] for w in st.session_state.watchlist]
    if ticker not in existing:
        st.session_state.watchlist.append(entry)
        return True
    return False

def remove_from_watchlist(ticker):
    """Entfernt Ticker von Watchlist"""
    st.session_state.watchlist = [w for w in st.session_state.watchlist if w["ticker"] != ticker]

def calculate_sr_levels(price):
    """Berechnet Support/Resistance basierend auf runden Zahlen"""
    if price <= 0:
        return [], []
    
    # Magnitude bestimmen
    if price >= 1000:
        step = 50
    elif price >= 100:
        step = 10
    elif price >= 10:
        step = 1
    elif price >= 1:
        step = 0.1
    elif price >= 0.1:
        step = 0.01
    else:
        step = 0.001
    
    # Runde auf nächsten Step
    base = round(price / step) * step
    
    # Support-Level (unter dem Preis)
    supports = [
        round(base - step, 6),
        round(base - step * 2, 6),
        round(base - step * 3, 6),
    ]
    
    # Resistance-Level (über dem Preis)
    resistances = [
        round(base + step, 6),
        round(base + step * 2, 6),
        round(base + step * 3, 6),
    ]
    
    return supports, resistances

# =============================================================================
# 4. DATA FETCHING FUNCTIONS
# =============================================================================
def fetch_crypto_data():
    results = []
    skipped_filter = 0
    
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd", "order": "market_cap_desc",
            "per_page": 250, "page": 1, "sparkline": False,
            "price_change_percentage": "24h"
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
                
                change_24h = coin.get("price_change_percentage_24h") or 0
                high_24h = coin.get("high_24h") or price
                low_24h = coin.get("low_24h") or price
                vol_24h = coin.get("total_volume") or 0
                market_cap = coin.get("market_cap") or 1
                
                if market_cap > 0:
                    vol_ratio = (vol_24h / market_cap) * 100
                    rvol = round(vol_ratio * 5, 2)
                    rvol = max(0.1, min(rvol, 100))
                else:
                    rvol = 1.0
                
                vortag_chg = change_24h
                close_pos = calculate_close_position(high_24h, low_24h, price)
                
                match = True
                if "RVOL" in f:
                    rvol_min, rvol_max = f["RVOL"]
                    if af.get("rvol_override_min"): rvol_min = af["rvol_override_min"]
                    if af.get("rvol_override_max"): rvol_max = af["rvol_override_max"]
                    if not (rvol_min <= rvol <= rvol_max): match = False
                
                if "Change %" in f and not (f["Change %"][0] <= change_24h <= f["Change %"][1]): match = False
                if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]): match = False
                if "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]): match = False
                if "Close Position" in f and not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]): match = False
                
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
                    "Ticker": ticker, "Name": coin.get("name", "")[:15],
                    "Preis": round(price, 6), "Chg%": round(change_24h, 2),
                    "RVOL": rvol, "Vortag%": round(vortag_chg, 2),
                    "ClosePos": round(close_pos, 2), "Alpha": alpha,
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
                
                high = day.get("h") or price
                low = day.get("l") or price
                close = day.get("c") or price
                
                change = t.get("todaysChangePerc")
                if change is None:
                    prev_close_price = prev.get("c") or price
                    change = ((price - prev_close_price) / prev_close_price) * 100 if prev_close_price > 0 else 0
                change = change or 0
                
                vol = day.get("v") or minute_data.get("v") or 0
                prev_vol = prev.get("v") or 0
                rvol = round(vol / prev_vol, 2) if prev_vol > 0 and vol > 0 else 1.0
                rvol = min(rvol, 999.0)
                
                prev_open = prev.get("o") or 0
                prev_close = prev.get("c") or 0
                vortag_chg = round(((prev_close - prev_open) / prev_open) * 100, 2) if prev_open > 0 else 0
                
                close_pos = calculate_close_position(high, low, close)
                
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
st.set_page_config(page_title="Alpha V49 Pro", layout="wide")

# AUTO-REFRESH (wenn aktiviert)
if st.session_state.auto_refresh_enabled:
    refresh_interval = st.session_state.get("refresh_interval", 5) * 60 * 1000  # in ms
    st_autorefresh(interval=refresh_interval, key="auto_refresh")

# -----------------------------------------------------------------------------
# SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("💎 Alpha V49 Pro")
    st.caption("Watchlist | Auto-Refresh | S/R Levels")
    
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
    
    if st.button("📥 Strategie laden", use_container_width=True):
        apply_strategy(strat)
        st.rerun()
    
    st.divider()
    
    # Aktive Filter
    if st.session_state.active_filters:
        st.subheader("⚙️ Filter")
        
        for filter_name, values in list(st.session_state.active_filters.items()):
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
            else:
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
        if not st.session_state.active_filters:
            st.warning("Erst Strategie laden!")
        else:
            with st.status(f"Scanne {m_type}...") as status:
                if m_type == "Krypto":
                    results, snp, sf = fetch_crypto_data()
                else:
                    poly_key = st.secrets["POLYGON_KEY"]
                    results, snp, sf = fetch_stock_data(poly_key)
                
                st.session_state.scan_results = sorted(results, key=lambda x: x["Alpha"], reverse=True)[:50]
                status.update(label=f"✅ {len(st.session_state.scan_results)} Signale", state="complete")

# -----------------------------------------------------------------------------
# HAUPTBEREICH - TABS
# -----------------------------------------------------------------------------
tab_scanner, tab_watchlist = st.tabs(["📊 Scanner", "⭐ Watchlist"])

with tab_scanner:
    col_chart, col_journal = st.columns([2, 1])
    
    with col_journal:
        st.subheader("📋 Ergebnisse")
        if st.session_state.current_strategy:
            st.caption(f"{st.session_state.current_strategy} | {st.session_state.market_type}")
        
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            display_cols = ["Ticker", "Preis", "Chg%", "RVOL", "Alpha"]
            if st.session_state.market_type == "Krypto" and "Name" in df.columns:
                display_cols = ["Ticker", "Name", "Preis", "Chg%", "Alpha"]
            
            sel = st.dataframe(
                df[display_cols], on_select="rerun", selection_mode="single-row",
                hide_index=True, use_container_width=True,
                column_config={
                    "Preis": st.column_config.NumberColumn("Preis", format="$%.4f"),
                    "Chg%": st.column_config.NumberColumn("Chg%", format="%.2f%%"),
                    "RVOL": st.column_config.NumberColumn("RVOL", format="%.1fx"),
                    "Alpha": st.column_config.NumberColumn("Alpha", format="%.0f⭐"),
                }
            )
            
            if sel.selection and sel.selection.rows:
                row = df.iloc[sel.selection.rows[0]]
                st.session_state.selected_symbol = str(row["Ticker"])
                st.session_state.current_data = row.to_dict()
                
                # S/R Levels berechnen
                supports, resistances = calculate_sr_levels(row["Preis"])
                st.session_state.sr_levels = {"support": supports, "resistance": resistances}
                
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
        
        # S/R LEVELS ANZEIGE
        if st.session_state.sr_levels["support"] or st.session_state.sr_levels["resistance"]:
            col_s, col_r = st.columns(2)
            with col_s:
                st.markdown("**🟢 Support**")
                for i, s in enumerate(st.session_state.sr_levels["support"], 1):
                    st.caption(f"S{i}: ${s:,.4f}")
            with col_r:
                st.markdown("**🔴 Resistance**")
                for i, r in enumerate(st.session_state.sr_levels["resistance"], 1):
                    st.caption(f"R{i}: ${r:,.4f}")
        
        # TradingView Chart
        if st.session_state.market_type == "Krypto":
            tv_symbol = f"BINANCE:{st.session_state.selected_symbol}USDT"
        else:
            tv_symbol = st.session_state.selected_symbol
        
        tv_html = f'''
        <div style="height:450px; border-radius: 8px; overflow: hidden;">
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
                    "enable_publishing": false,
                    "hide_side_toolbar": false,
                    "allow_symbol_change": true,
                    "studies": ["Volume@tv-basicstudies"],
                    "container_id": "tv_chart"
                }});
            </script>
        </div>
        '''
        st.components.v1.html(tv_html, height=450)

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
                
                sr_text = f"""
BERECHNETE SUPPORT/RESISTANCE:
Support: {', '.join([f'${s}' for s in sr['support']])}
Resistance: {', '.join([f'${r}' for r in sr['resistance']])}
"""
                
                prompt = f"""TECHNISCHES TERMINAL - ANALYSE

TICKER: {d['Ticker']}
STRATEGIE: {st.session_state.current_strategy}
MARKT: {m_type}

DATEN:
- Preis: ${d['Preis']}
- 24h Änderung: {d['Chg%']}%
- RVOL: {d['RVOL']}x
- Alpha-Score: {d['Alpha']}

{sr_text}

NEWS: {news_txt}

AUFGABEN:
1. Bewerte Setup für Strategie "{st.session_state.current_strategy}"
2. Validiere/korrigiere die S/R-Levels falls nötig
3. Risk/Reward Ratio
4. Rating 1-100
5. Empfehlung: LONG / SHORT / ABWARTEN

Keine Disclaimers. Nur Fakten."""

                message = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1500,
                    system="Du bist ein präzises Finanz-Terminal. Keine Höflichkeitsfloskeln. Die Daten sind Fakten.",
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
c1, c2, c3 = st.columns(3)
with c1:
    st.caption("Alpha Station V49 Pro")
with c2:
    st.caption(f"Watchlist: {len(st.session_state.watchlist)} Ticker")
with c3:
    if st.session_state.auto_refresh_enabled:
        st.caption(f"🔄 Auto-Refresh: {st.session_state.refresh_interval} Min")
    else:
        st.caption("🔄 Auto-Refresh: Aus")
