import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import os
import pytz
from streamlit_autorefresh import st_autorefresh
import streamlit.components.v1 as components

# --- 1. KONFIGURATION & DATEI-PFAD ---
API_KEY = "N4ayYDye9LBc5uy66WeEpCayxGebIEiF" 
TELEGRAM_TOKEN = "8362129761:AAHFebiHtpuL_QSU1okcudEnIGWAvyZM4IE"
TELEGRAM_CHAT_ID = "93372553"
DB_FILE = "signal_history.csv"

st.set_page_config(layout="wide", page_title="ALPHA MASTER V32", page_icon="💾")
st_autorefresh(interval=30000, key="alpha_persistence_v32")

# --- 2. PERSISTENZ-LOGIK (LADEN & SPEICHERN) ---

def load_journal():
    if os.path.exists(DB_FILE):
        try:
            return pd.read_csv(DB_FILE)
        except:
            return pd.DataFrame(columns=['Time', 'Ticker', 'Price', 'Gap%', 'Signal', 'Sentiment', 'Info'])
    return pd.DataFrame(columns=['Time', 'Ticker', 'Price', 'Gap%', 'Signal', 'Sentiment', 'Info'])

def save_journal(df):
    df.to_csv(DB_FILE, index=False)

# Initialisierung des Journals beim Start
if 'signal_journal' not in st.session_state:
    st.session_state.signal_journal = load_journal()

# --- 3. HILFSFUNKTIONEN ---

def fetch_safe_data(endpoint, params=""):
    url = f"https://financialmodelingprep.com/api/v3/{endpoint}?{params}&apikey={API_KEY}"
    try:
        r = requests.get(url, timeout=5)
        return r.json() if r.status_code == 200 else None
    except: return None

def fetch_batch_quotes(tickers):
    data = fetch_safe_data(f"quote/{','.join(tickers).upper()}")
    return data if isinstance(data, list) else []

def get_tv_widget(symbol, height=500):
    return f"""<div style="height:{height}px;width:100%; border: 1px solid #333; border-radius: 8px; overflow: hidden;">
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">new TradingView.widget({{"autosize": true, "symbol": "{symbol}", "interval": "5", "theme": "dark", "container_id": "tv_{symbol}"}});</script>
      <div id="tv_{symbol}" style="height:100%;"></div></div>"""

# --- 4. SIDEBAR NAVIGATION ---
st.sidebar.title("💾 Alpha Master V32")
app_mode = st.sidebar.radio("Navigation", ["📡 Live Radar", "📊 Backtest", "🧠 AI Research"])

st.sidebar.markdown("---")

if app_mode == "📡 Live Radar":
    st.sidebar.subheader("Scanner & Strategien")
    strat_1 = st.sidebar.selectbox("Strategie 1 (Momentum)", ["Gap Momentum", "RVOL Spike", "Low Float Winners", "New Highs"])
    strat_2 = st.sidebar.selectbox("Strategie 2 (Filter)", ["Keine", "EMA 200 Touch", "Relative Stärke > SPY", "RSI < 70"])
    scan_limit = st.sidebar.slider("Markt-Tiefe", 100, 2000, 500)
    tg_enabled = st.sidebar.toggle("Telegram Alarme 📱", value=True)
    manual_scan = st.sidebar.button("🚀 SCANNER JETZT STARTEN", type="primary")

if st.sidebar.button("Journal & Datei löschen"):
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    st.session_state.signal_journal = pd.DataFrame(columns=['Time', 'Ticker', 'Price', 'Gap%', 'Signal', 'Sentiment', 'Info'])
    st.rerun()

# --- 5. SCAN LOGIK ---
tz_ch = pytz.timezone('Europe/Zurich')
now_ch = datetime.now(tz_ch).strftime("%H:%M:%S")

def run_market_scan():
    ticker_list = fetch_safe_data("stock/list")
    if isinstance(ticker_list, list):
        valid = [t['symbol'] for t in ticker_list if isinstance(t, dict) and t.get('type') == 'stock'][:scan_limit]
        for i in range(0, len(valid), 50):
            quotes = fetch_batch_quotes(valid[i:i+50])
            for s in quotes:
                p, sym = s.get('price', 0), s.get('symbol')
                if not p or p < 1.0: continue
                
                prev_c = s.get('previousClose', 1)
                gap = round(((p - prev_c)/prev_c)*100, 2)
                rv = round(s.get('volume', 0) / (s.get('avgVolume', 1)), 2) if s.get('avgVolume', 0) > 0 else 0
                mkt_cap = s.get('marketCap', 0) / 1_000_000
                
                hit = False
                if strat_1 == "Gap Momentum" and abs(gap) >= 3.0: hit = True
                elif strat_1 == "RVOL Spike" and rv >= 2.0: hit = True
                elif strat_1 == "Low Float Winners" and mkt_cap < 500 and gap > 2.0: hit = True
                
                if hit and sym not in st.session_state.signal_journal['Ticker'].values:
                    entry = {'Time': now_ch, 'Ticker': sym, 'Price': p, 'Gap%': gap, 'Signal': strat_1, 'Sentiment': 'Neutral', 'Info': ''}
                    # Session State und CSV aktualisieren
                    st.session_state.signal_journal = pd.concat([st.session_state.signal_journal, pd.DataFrame([entry])], ignore_index=True)
                    save_journal(st.session_state.signal_journal)
                    if tg_enabled:
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                     json={"chat_id": TELEGRAM_CHAT_ID, "text": f"🚀 SIGNAL: {sym}\nGap: {gap}% | {strat_1}"})

# --- 6. MAIN UI ---
st.title(f"⚡ Alpha Master Station: {app_mode}")

if app_mode == "📡 Live Radar":
    run_market_scan() # Auto-Scan
    
    col_l, col_r = st.columns([2, 1])
    with col_l:
        st.subheader("🌐 Sektor Performance (Live)")
        s_data = fetch_batch_quotes(["XLK", "XLF", "XLE", "XLV", "XLY"])
        if isinstance(s_data, list):
            s_list = [{"Sektor": s['symbol'], "Perf%": s.get('changesPercentage', 0)} for s in s_data if isinstance(s, dict)]
            if s_list:
                fig_s = px.bar(pd.DataFrame(s_list), x="Sektor", y="Perf%", color="Perf%", color_continuous_scale="RdYlGn")
                fig_s.update_layout(template="plotly_dark", height=280)
                st.plotly_chart(fig_s, use_container_width=True)

        if not st.session_state.signal_journal.empty:
            top_ticker = st.session_state.signal_journal.iloc[-1]['Ticker']
            st.subheader(f"🔍 Fokus: {top_ticker}")
            components.html(get_tv_widget(top_ticker), height=500)
        else:
            st.info("Scanner aktiv. Erwarte Signale...")
            components.html(get_tv_widget("SPY"), height=500)

    with col_r:
        st.subheader("📝 Signal Journal (Historisch gespeichert)")
        st.dataframe(st.session_state.signal_journal.sort_index(ascending=False), use_container_width=True, height=600)

st.caption(f"V32 Persistent | 2025-12-30 | Gerlikon | US-Premarket LÄUFT!")