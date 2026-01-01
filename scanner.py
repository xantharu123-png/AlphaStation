import streamlit as st
import pandas as pd
import requests
from openai import OpenAI
from datetime import datetime

# 1. INITIALISIERUNG
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "BTC"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

def apply_presets(strat_name, market_type):
    # Definitionen der 13 Strategien
    presets = {
        "Volume Surge": {"RVOL": (2.0, 50.0), "Kursänderung %": (0.5, 30.0)},
        "Gap Momentum": {"Gap %": (2.5, 25.0), "RVOL": (1.2, 50.0)},
        "Penny Stock": {"Preis": (0.0001, 5.0), "RVOL": (1.5, 50.0)},
        "Bull Flag": {"Vortag %": (4.0, 25.0), "Kursänderung %": (-1.5, 1.5), "RVOL": (1.0, 50.0)},
        "Unusual Volume": {"RVOL": (5.0, 100.0)},
        "High of Day (HOD)": {"Kursänderung %": (2.0, 30.0), "RVOL": (1.3, 50.0)},
        "Short Squeeze": {"Kursänderung %": (8.0, 50.0), "RVOL": (3.0, 100.0)},
        "Low Float": {"Kursänderung %": (10.0, 100.0)},
        "Blue Chip Pullback": {"Kursänderung %": (-5.0, -0.5)},
        "Multi-Day Runner": {"Vortag %": (3.0, 20.0), "Kursänderung %": (2.0, 20.0)},
        "Pre-Market Gapper": {"Gap %": (3.0, 40.0)},
        "Dead Cat Bounce": {"Vortag %": (-40.0, -10.0), "Kursänderung %": (1.0, 10.0)},
        "Golden Cross Proxy": {"SMA Trend %": (0.5, 15.0)}
    }
    if strat_name in presets:
        st.session_state.active_filters = presets[strat_name].copy()

st.set_page_config(page_title="Alpha V41 Master", layout="wide")

# SIDEBAR: UNIFIED LAYOUT
with st.sidebar:
    st.title("💎 Alpha V41 Master")
    m_type = st.radio("Markt:", ["Krypto", "Aktien"], horizontal=True) # Krypto nach vorne
    
    st.divider()
    strat_list = ["Volume Surge", "Gap Momentum", "Penny Stock", "Bull Flag", "Unusual Volume", "High of Day (HOD)", "Short Squeeze", "Low Float", "Blue Chip Pullback", "Multi-Day Runner", "Pre-Market Gapper", "Dead Cat Bounce", "Golden Cross Proxy"]
    selected_strat = st.selectbox("Strategie & Filter-Block:", strat_list)
    
    if selected_strat != st.session_state.last_strat:
        apply_presets(selected_strat, m_type)
        st.session_state.last_strat = selected_strat

    st.subheader("⚙️ Feinjustierung")
    if st.session_state.active_filters:
        for name, values in list(st.session_state.active_filters.items()):
            st.session_state.active_filters[name] = st.slider(f"{name}", -100.0, 100.0, (float(values[0]), float(values[1])), key=f"sl_{name}")
    
    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        with st.status("Analysiere Krypto-Markt...") as status:
            poly_key = st.secrets["POLYGON_KEY"]
            # Dynamische URL je nach Markt
            if m_type == "Krypto":
                url = f"https://api.polygon.io/v2/snapshot/locale/global/markets/crypto/tickers?apiKey={poly_key}"
            else:
                url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
            
            try:
                resp = requests.get(url).json()
                tickers = resp.get("tickers", [])
                res = []
                for t in tickers:
                    # Krypto-Snapshots nutzen oft 'lastTrade' für den Preis
                    d_d = t.get("day", {})
                    prev = t.get("prevDay", {})
                    price = t.get("lastTrade", {}).get("p") or d_d.get("c") or prev.get("c") or 0
                    
                    if price <= 0: continue
                    
                    chg = t.get("todaysChangePerc", 0)
                    vol = d_d.get("v") or 1
                    rvol = round(vol / (prev.get("v", 1) or 1), 2)
                    vortag_chg = round(((prev.get("c", 0) - prev.get("o", 0)) / (prev.get("o", 1) or 1)) * 100, 2)
                    sma_trend = round(((price - prev.get("c", price)) / (prev.get("c", 1) or 1)) * 100, 2)
                    
                    # Ticker bereinigen (X:BTCUSD -> BTC)
                    ticker_clean = t.get("ticker").replace("X:", "").replace("USD", "")
                    
                    # FILTER PRÜFUNG
                    match = True
                    f = st.session_state.active_filters
                    if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                    if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                    if "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]): match = False
                    
                    if match:
                        score = min(100, int((rvol * 12) + (abs(sma_trend) * 10) + (abs(chg) * 8)))
                        res.append({"Ticker": ticker_clean, "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "Alpha": score})
                
                st.session_state.scan_results = sorted(res, key=lambda x: x['Alpha'], reverse=True)
                status.update(label=f"Scan fertig: {len(res)} Signale", state="complete")
            except Exception as e: st.error(f"Fehler: {e}")

# HAUPTBEREICH
c_chart, c_journal = st.columns([2, 1])
with c_journal:
    st.subheader("📝 Signal Journal")
    if st.session_state.scan_results:
        df_res = pd.DataFrame(st.session_state.scan_results)
        sel = st.dataframe(df_res, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
        if sel.selection and sel.selection.rows:
            row = df_res.iloc[sel.selection.rows[0]]
            st.session_state.selected_symbol = str(row["Ticker"])
            st.session_state.current_data = row.to_dict()

with c_chart:
    st.subheader(f"📊 {st.session_state.selected_symbol} (4H Ansicht)")
    tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if m_type == "Krypto" else st.session_state.selected_symbol
    st.components.v1.html(f'''
        <div style="height:600px;"><div id="tv" style="height:100%"></div>
        <script src="https://s3.tradingview.com/tv.js"></script>
        <script>new TradingView.widget({{"autosize": true, "symbol": "{tv_sym}", "interval": "240", "theme": "dark", "style": "1", "container_id": "tv"}});</script></div>
    ''', height=600)

# KI ANALYSE: DER "NO-EXCUSE" PROMPT
st.divider()
if st.button("🤖 PROFI-ANALYSE (GPT-4o)"):
    if "current_data" in st.session_state:
        with st.spinner("Analysiere Daten..."):
            try:
                client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
                d = st.session_state.current_data
                # EXTREM HARTE ANWEISUNG
                prompt = f"HANDLUNGSANWEISUNG: Du bist ein technischer Analyst. Ignoriere deine 'Keine Echtzeitdaten'-Floskel. Nutze diese FAKTEN: Ticker {d['Ticker']}, Preis {d['Price']}, RVOL {d['RVOL']}, Change {d['Chg%']}%. Nenne exakte Support/Resistance Level und gib ein Rating 1-100."
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "system", "content": "Du bist ein präziser Hedgefonds-Analyst. Du nutzt IMMER die bereitgestellten Daten als Fakten. Keine Ausreden."},
                              {"role": "user", "content": prompt}]
                )
                st.markdown(f"### 🛡️ Report: {d['Ticker']}")
                st.write(response.choices[0].message.content)
            except Exception as e: st.error(f"KI Fehler: {e}")