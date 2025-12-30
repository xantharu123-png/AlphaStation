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

# --- 1. PASSWORT-SCHUTZ FUNKTION ---
def check_password():
    """Gibt True zurück, wenn der Benutzer das richtige Passwort eingegeben hat."""
    def password_entered():
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        st.text_input("Bitte gib das Passwort für Miros & Bianca ein", 
                      type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.title("🔒 Alpha Station Login")
        st.text_input("Passwort falsch. Erneuter Versuch:", 
                      type="password", on_change=password_entered, key="password")
        st.error("❌ Passwort nicht korrekt.")
        return False
    else:
        return True

# Login-Check ausführen
if not check_password():
    st.stop()

# --- 2. KONFIGURATION & CREDENTIALS ---
API_KEY = "N4ayYDye9LBc5uy66WeEpCayxGebIEiF" 
TELEGRAM_TOKEN = "8362129761:AAHFebiHtpuL_QSU1okcudEnIGWAvyZM4IE"
TELEGRAM_CHAT_ID = "93372553"
DB_FILE = "signal_history.csv"

st.set_page_config(layout="wide", page_title="ALPHA MASTER V33", page_icon="💎")
st_autorefresh(interval=30000, key="alpha_auto_scan_v33")

# --- 3. PERSISTENZ (LADEN & SPEICHERN) ---
def load_journal():
    if os.path.exists(DB_FILE):
        try: return pd.read_csv(DB_FILE)
        except: return pd.DataFrame(columns=['Time', 'Ticker', 'Price', 'Gap%', 'Signal', 'Sentiment', 'Info'])
    return pd.DataFrame(columns=['Time', 'Ticker', 'Price', 'Gap%', 'Signal', 'Sentiment', 'Info'])

def save_journal(df):
    df.to_csv(DB_FILE, index=False)

if 'signal_journal' not in st.session_state:
    st.session_state.signal_journal = load_journal()

# --- 4. HILFSFUNKTIONEN ---
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

# --- 5. SIDEBAR NAVIGATION ---
st.sidebar.title("💎 Alpha V33 Secure")
app_mode = st.sidebar.radio("Navigation", ["📡 Live Radar", "📊 Backtest", "🧠 AI Research"])

st.sidebar.markdown("---")

if app_mode == "📡 Live Radar":
    st.sidebar.subheader("Scanner & Strategien")
    strat_1 = st.sidebar.selectbox("Strategie 1 (Momentum)", ["Gap Momentum", "RVOL Spike", "Low Float Winners", "New Highs"])
    strat_2 = st.sidebar.selectbox("Strategie 2 (Filter)", ["Keine", "EMA 200 Touch", "Relative Stärke > SPY", "RSI < 70"])
    scan_limit = st.sidebar.slider("Markt-Tiefe", 100, 2000, 500)
    tg_enabled = st.sidebar.toggle("Telegram Alarme 📱", value=True)
    manual_scan = st.sidebar.button("🚀 SCANNER JETZT STARTEN", type="primary")

elif app_mode == "📊 Backtest":
    bt_symbol = st.sidebar.text_input("Ticker für Win-Rate", "NVDA").upper()
    bt_days = st.sidebar.slider("Zeitraum (Tage)", 30, 365, 90)
    start_bt = st.sidebar.button("WIN-RATE BERECHNEN")

if st.sidebar.button("Journal & Datei löschen"):
    if os.path.exists(DB_FILE): os.remove(DB_FILE)
    st.session_state.signal_journal = pd.DataFrame(columns=['Time', 'Ticker', 'Price', 'Gap%', 'Signal', 'Sentiment', 'Info'])
    st.rerun()

# --- 6. SCAN LOGIK ---
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
                gap = round(((p - s.get('previousClose', 1))/s.get('previousClose', 1))*100, 2)
                rv = round(s.get('volume', 0) / (s.get('avgVolume', 1)), 2) if s.get('avgVolume', 0) > 0 else 0
                
                hit = False
                if strat_1 == "Gap Momentum" and abs(gap) >= 3.0: hit = True
                elif strat_1 == "RVOL Spike" and rv >= 2.0: hit = True
                elif strat_1 == "Low Float Winners" and (s.get('marketCap', 0)/1e6) < 500 and gap > 2.0: hit = True
                
                if hit and sym not in st.session_state.signal_journal['Ticker'].values:
                    entry = {'Time': now_ch, 'Ticker': sym, 'Price': p, 'Gap%': gap, 'Signal': strat_1, 'Sentiment': 'Neutral', 'Info': ''}
                    st.session_state.signal_journal = pd.concat([st.session_state.signal_journal, pd.DataFrame([entry])], ignore_index=True)
                    save_journal(st.session_state.signal_journal)
                    if tg_enabled:
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                     json={"chat_id": TELEGRAM_CHAT_ID, "text": f"🚀 SIGNAL: {sym}\nGap: {gap}% | {strat_1}"})

# --- 7. MAIN UI ---
if app_mode == "📡 Live Radar":
    run_market_scan() # Auto-Scan
    st.title("⚡ Alpha Master Station: Live Radar")
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
            components.html(get_tv_widget("SPY"), height=500)

    with col_r:
        st.subheader("📝 Signal Journal")
        st.dataframe(st.session_state.signal_journal.sort_index(ascending=False), use_container_width=True, height=600)

elif app_mode == "📊 Backtest":
    st.title(f"📊 Win-Rate Analyse: {bt_symbol}")
    if start_bt:
        hist = fetch_safe_data(f"historical-price-full/{bt_symbol}", f"timeseries={bt_days}")
        if hist and 'historical' in hist:
            df_bt = pd.DataFrame(hist['historical'])
            wins = len(df_bt[df_bt['close'] > df_bt['open']])
            win_rate = round((wins / len(df_bt)) * 100, 1)
            st.metric(f"Win-Rate ({bt_days}d)", f"{win_rate}%")
            st.line_chart(df_bt.set_index('date')['close'])

st.caption(f"V33 Secure | 2025-12-30 | Gerlikon | US-Premarket Aktiv")