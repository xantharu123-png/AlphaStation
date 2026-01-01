import streamlit as st
import pandas as pd
import requests
from openai import OpenAI
from datetime import datetime

# 1. INITIALISIERUNG
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "BTC"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

def apply_presets(strat_name, market_type):
    # Alle 13 mathematischen Strategien
    presets = {
        "Volume Surge": {"RVOL": (2.0, 50.0), "Kursänderung %": (0.5, 30.0)},
        "Gap Momentum": {"Gap %": (2.5, 25.0), "RVOL": (1.2, 50.0)},
        "Penny Stock/Moon Shot": {"Preis min-max": (0.0001, 5.0) if market_type == "Krypto" else (0.5, 5.0)},
        "Bull Flag Breakout": {"Vortag %": (4.0, 20.0), "Kursänderung %": (-1.0, 2.0)},
        "Unusual Volume": {"RVOL": (5.0, 100.0)},
        "High of Day (HOD)": {"Abstand vom Hoch %": (0.0, 0.5), "RVOL": (1.3, 50.0)},
        "Short Squeeze Candidate": {"Kursänderung %": (8.0, 45.0), "RVOL": (3.0, 50.0)},
        "Low Float/Market Cap": {"Market Cap (Mrd $)": (0.0, 0.5), "Kursänderung %": (10.0, 100.0)},
        "Blue Chip Pullback": {"Kursänderung %": (-4.0, -0.2)},
        "Multi-Day Runner": {"Vortag %": (2.0, 15.0), "Kursänderung %": (2.0, 15.0)},
        "Pre-Market Gapper": {"Gap %": (4.0, 40.0)},
        "Dead Cat Bounce": {"Vortag %": (-40.0, -9.0), "Kursänderung %": (1.5, 12.0)},
        "Golden Cross Proxy": {"SMA Trend": (0.5, 10.0)}
    }
    if strat_name in presets:
        st.session_state.active_filters = presets[strat_name].copy()

# LOGIN
if "password_correct" not in st.session_state:
    st.title("🔒 Alpha Station Login")
    with st.form("login"):
        if st.form_submit_button("Einloggen") and st.text_input("PW", type="password") == st.secrets.get("PASSWORD"):
            st.session_state["password_correct"] = True
            st.rerun()
    st.stop()

st.set_page_config(page_title="Alpha Master Pro", layout="wide")

# SIDEBAR
with st.sidebar:
    st.title("💎 Alpha V33 OpenAI")
    m_type = st.radio("Markt:", ["Aktien", "Krypto"], horizontal=True)
    
    st.divider()
    strat_list = ["Volume Surge", "Gap Momentum", "Penny Stock/Moon Shot", "Bull Flag Breakout", "Unusual Volume", "High of Day (HOD)", "Short Squeeze Candidate", "Low Float/Market Cap", "Blue Chip Pullback", "Multi-Day Runner", "Pre-Market Gapper", "Dead Cat Bounce", "Golden Cross Proxy"]
    main_strat = st.selectbox("Strategie wählen", sorted(strat_list))
    if main_strat != st.session_state.last_strat:
        apply_presets(main_strat, m_type); st.session_state.last_strat = main_strat

    # FEINJUSTIERUNG
    st.divider()
    st.subheader("⚙️ Feinjustierung")
    f_type = st.selectbox("Parameter", ["Kursänderung %", "RVOL", "SMA Trend", "Preis min-max"])
    if f_type == "RVOL": val = st.slider("RVOL", 0.0, 50.0, (1.5, 5.0), key=f"sl_{f_type}")
    elif f_type == "SMA Trend": val = st.slider("SMA %", -20.0, 20.0, (0.5, 3.0), key=f"sl_{f_type}")
    else: val = st.slider("Bereich", -100.0, 100.0, (0.0, 10.0), key=f"sl_{f_type}")
    if st.button("➕ Hinzufügen"):
        st.session_state.active_filters[f_type] = val; st.rerun()

    if st.session_state.active_filters:
        for n, v in list(st.session_state.active_filters.items()):
            if st.button(f"× {n}: {v[0]}-{v[1]}", key=f"btn_{n}"):
                del st.session_state.active_filters[n]; st.rerun()

    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        with st.status("Suche Signale...") as status:
            poly_key = st.secrets["POLYGON_KEY"]
            url = f"https://api.polygon.io/v2/snapshot/locale/{'global' if m_type=='Krypto' else 'us'}/markets/{'crypto' if m_type=='Krypto' else 'stocks'}/tickers?apiKey={poly_key}"
            try:
                resp = requests.get(url).json()
                res = []
                for t in resp.get("tickers", []):
                    d_d = t.get("day", {})
                    price = d_d.get("c") or t.get("lastTrade", {}).get("p") or t.get("min", {}).get("c", 0)
                    if price <= 0: continue
                    chg, vol, prev = t.get("todaysChangePerc", 0), d_d.get("v", 1), t.get("prevDay", {})
                    rvol = round(vol / (prev.get("v", 1) or 1), 2)
                    sma_trend = round(((price - (prev.get("c") or price)) / (prev.get("c") or 1)) * 100, 2)
                    match = True
                    f = st.session_state.active_filters
                    if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                    if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                    if match:
                        score = min(100, int((rvol * 12) + (abs(sma_trend) * 10) + (abs(chg) * 8)))
                        res.append({"Ticker": t.get("ticker").replace("X:", ""), "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "Alpha-Score": score})
                st.session_state.scan_results = sorted(res, key=lambda x: x['Alpha-Score'], reverse=True)
                
                # MIROSLAV REGEL [2025-12-28]
                if len(st.session_state.scan_results) < 30:
                    st.warning("Hey, ich habe leider keine 30 Spiele gefunden, aber hier sind trotzdem meine Empfehlungen.")
                status.update(label="Scan fertig", state="complete")
            except: st.error("API Fehler")

    # SUCHE & FAVORITEN
    st.divider()
    st.subheader("🔍 Suche & Favoriten")
    search_ticker = st.text_input("Symbol", "").upper()
    if st.button("LADEN", use_container_width=True): st.session_state.selected_symbol = search_ticker
    if st.button("⭐ FAVORIT", use_container_width=True):
        if st.session_state.selected_symbol not in st.session_state.watchlist:
            st.session_state.watchlist.append(st.session_state.selected_symbol); st.rerun()

    for w in list(set(st.session_state.watchlist)):
        wc1, wc2 = st.columns([4, 1])
        if wc1.button(f"📌 {w}", key=f"ws_{w}"): st.session_state.selected_symbol = w
        if wc2.button("×", key=f"wd_{w}"): st.session_state.watchlist.remove(w); st.rerun()

# TERMINAL
t1, t2, t3 = st.tabs(["🚀 Terminal", "📅 Kalender", "📊 Sektoren"])
with t1:
    c_chart, c_journal = st.columns([2, 1])
    with c_journal:
        st.subheader("📝 Journal")
        if st.session_state.scan_results:
            df_res = pd.DataFrame(st.session_state.scan_results)
            sel = st.dataframe(df_res, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
            if sel.selection and sel.selection.rows: st.session_state.selected_symbol = str(df_res.iloc[sel.selection.rows[0]]["Ticker"])
    with c_chart:
        st.subheader(f"📊 Live-Preis: {st.session_state.selected_symbol}")
        # CHART FIX: Binance Preis
        tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if m_type == "Krypto" else st.session_state.selected_symbol
        st.components.v1.html(f'<div style="height:750px;width:100%"><div id="tv" style="height:100%"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize": true, "symbol": "{tv_sym}", "interval": "5", "theme": "dark", "style": "1", "locale": "de", "container_id": "tv"}});</script></div>', height=750)

# --- KI-ANALYSE MIT OPENAI ---
st.divider()
if st.button("🤖 KI ANALYSE STARTEN"):
    with st.spinner("GPT-4o analysiert..."):
        try:
            client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
            # Rating und Analyse-Regel [cite: 2025-12-30]
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": f"Analysiere {st.session_state.selected_symbol}. Gib ein KI-Rating von 1-100 basierend auf Preis und Volumen."}],
                temperature=0.7
            )
            st.info(response.choices[0].message.content)
        except Exception as e:
            st.error(f"OpenAI Fehler: {e}. Prüfe deinen Key in den Secrets!")

st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")