import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "BTC"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

def calculate_alpha_score(rvol, sma_trend, chg):
    # Mathematische Priorisierung
    score = (rvol * 12) + (abs(sma_trend) * 10) + (abs(chg) * 8)
    return min(100, max(1, int(score)))

def get_sector_performance(poly_key):
    # Sektoren-Matrix
    sectors = {"Tech": "XLK", "Energy": "XLE", "Finance": "XLF", "Health": "XLV", "Retail": "XLY"}
    results = []
    for name, ticker in sectors.items():
        try:
            url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey={poly_key}"
            d = requests.get(url).json()
            results.append({"Sektor": name, "Performance %": round(d.get("ticker", {}).get("todaysChangePerc", 0), 2)})
        except: continue
    return pd.DataFrame(results)

if "password_correct" not in st.session_state:
    st.title("🔒 Alpha Login")
    with st.form("login"):
        if st.form_submit_button("Login") and st.text_input("PW", type="password") == st.secrets.get("PASSWORD"):
            st.session_state["password_correct"] = True
            st.rerun()
    st.stop()

st.set_page_config(page_title="Alpha Master Pro", layout="wide")

with st.sidebar:
    st.title("💎 Alpha V33 Master")
    m_type = st.radio("Markt:", ["Aktien", "Krypto"], horizontal=True)
    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        with st.status("Scanne...") as status:
            poly_key = st.secrets["POLYGON_KEY"]
            url = f"https://api.polygon.io/v2/snapshot/locale/{'us' if m_type=='Aktien' else 'global'}/markets/{'stocks' if m_type=='Aktien' else 'crypto'}/tickers?apiKey={poly_key}"
            try:
                resp = requests.get(url).json()
                res = []
                for t in resp.get("tickers", []):
                    ticker_data = t.get("min", {}) or t.get("lastTrade", {})
                    price = ticker_data.get("c") or ticker_data.get("p", 0)
                    chg = t.get("todaysChangePerc", 0)
                    prev = t.get("prevDay", {})
                    rvol = round(t.get("day", {}).get("v", 0) / (prev.get("v", 1) or 1), 2)
                    sma_trend = round(((price - ((prev.get("h", 0)+prev.get("l", 0)+prev.get("c", 0))/3 or 1)) / (prev.get("c", 1) or 1)) * 100, 2)
                    if price > 0:
                        res.append({"Ticker": t.get("ticker").replace("X:", ""), "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "Alpha-Score": calculate_alpha_score(rvol, sma_trend, chg)})
                st.session_state.scan_results = sorted(res, key=lambda x: x['Alpha-Score'], reverse=True)
                if len(st.session_state.scan_results) < 30:
                    st.warning("Hey, ich habe leider keine 30 Spiele gefunden, aber hier sind trotzdem meine Empfehlungen. [cite: 2025-12-28]")
                status.update(label="Scan fertig", state="complete")
            except Exception as e: st.error(f"Fehler: {e}")

    st.divider()
    search = st.text_input("Suche").upper()
    if st.button("LADEN") and search: st.session_state.selected_symbol = search
    if st.button("⭐ FAVORIT"): st.session_state.watchlist.append(st.session_state.selected_symbol)
    for w in list(set(st.session_state.watchlist)):
        if st.sidebar.button(f"📌 {w}", key=f"side_{w}"): st.session_state.selected_symbol = w

t1, t2, t3 = st.tabs(["🚀 Terminal", "📅 Kalender", "📊 Sektoren"])
with t1:
    c1, c2 = st.columns([2, 1])
    with c2:
        if st.session_state.scan_results:
            df_res = pd.DataFrame(st.session_state.scan_results)
            sel = st.dataframe(df_res, on_select="rerun", selection_mode="single-row", hide_index=True)
            if sel.selection and sel.selection.rows: st.session_state.selected_symbol = df_res.iloc[sel.selection.rows[0]]["Ticker"]
    with c1:
        st.subheader(f"📊 {st.session_state.selected_symbol}")
        tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if m_type == "Krypto" else st.session_state.selected_symbol
        st.components.v1.html(f'<div style="height:750px;width:100%"><div id="tv" style="height:100%"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize": true, "symbol": "{tv_sym}", "interval": "5", "theme": "dark", "style": "1", "locale": "de", "container_id": "tv"}});</script></div>', height=750)

with t3:
    if m_type == "Aktien": st.dataframe(get_sector_performance(st.secrets["POLYGON_KEY"]), use_container_width=True)
    else: st.info("Sektoren-Matrix nur für Aktien.")

st.divider()
if st.button("🤖 KI ANALYSE"):
    with st.spinner("Gemini analysiert..."):
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-1.5-flash')
        st.info(model.generate_content(f"Analysiere {st.session_state.selected_symbol}. Gib ein KI-Rating 1-100. [cite: 2025-12-30]").text)

st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")