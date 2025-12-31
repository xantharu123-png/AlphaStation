import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

# 1. INITIALISIERUNG
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "BTC"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

def apply_presets(strat_name, market_type):
    # Alle 13 Strategien
    presets = {
        "Volume Surge": {"RVOL": (2.0, 50.0), "Kursänderung %": (0.5, 30.0)},
        "Gap Momentum": {"Gap %": (2.5, 25.0), "RVOL": (1.2, 50.0)},
        "Penny Stock/Moon Shot": {"Preis min-max": (0.0001, 5.0) if market_type == "Krypto" else (0.5, 5.0), "Volumen": (1000000, 50000000000)},
        "Bull Flag Breakout": {"Vortag %": (4.0, 20.0), "Kursänderung %": (-1.0, 2.0)},
        "Unusual Volume": {"RVOL": (5.0, 100.0), "Volumen": (2000000, 50000000000)},
        "High of Day (HOD)": {"Abstand vom Hoch %": (0.0, 0.5), "RVOL": (1.3, 50.0)},
        "Short Squeeze Candidate": {"Kursänderung %": (8.0, 45.0), "RVOL": (3.0, 50.0)},
        "Low Float/Market Cap": {"Market Cap (Mrd $)": (0.0, 0.5), "Kursänderung %": (10.0, 100.0)},
        "Blue Chip Pullback": {"Market Cap (Mrd $)": (50.0, 3000.0), "Kursänderung %": (-4.0, -0.2)},
        "Multi-Day Runner": {"Vortag %": (2.0, 15.0), "Kursänderung %": (2.0, 15.0)},
        "Pre-Market Gapper": {"Gap %": (4.0, 40.0), "Volumen": (100000, 50000000000)},
        "Dead Cat Bounce": {"Vortag %": (-40.0, -9.0), "Kursänderung %": (1.5, 12.0)},
        "Golden Cross Proxy": {"SMA Trend": (0.5, 10.0), "Kursänderung %": (0.5, 10.0)}
    }
    if strat_name in presets:
        st.session_state.active_filters = presets[strat_name].copy()

def calculate_alpha_score(rvol, sma_trend, chg):
    score = (rvol * 12) + (abs(sma_trend) * 10) + (abs(chg) * 8)
    return min(100, max(1, int(score)))

def get_sector_performance(poly_key):
    sectors = {"Tech": "XLK", "Energy": "XLE", "Finance": "XLF", "Health": "XLV", "Retail": "XLY"}
    results = []
    for name, ticker in sectors.items():
        try:
            url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey={poly_key}"
            d = requests.get(url).json()
            results.append({"Sektor": name, "Performance %": round(d.get("ticker", {}).get("todaysChangePerc", 0), 2)})
        except: continue
    return pd.DataFrame(results)

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
    st.title("💎 Alpha V33 Master")
    m_type = st.radio("Märkte:", ["Aktien", "Krypto"], horizontal=True)
    
    st.divider()
    strat_list = ["Volume Surge", "Gap Momentum", "Penny Stock/Moon Shot", "Bull Flag Breakout", "Unusual Volume", "High of Day (HOD)", "Short Squeeze Candidate", "Low Float/Market Cap", "Blue Chip Pullback", "Multi-Day Runner", "Pre-Market Gapper", "Dead Cat Bounce", "Golden Cross Proxy"]
    main_strat = st.selectbox("Strategie-Rezept", strat_list)
    if main_strat != st.session_state.last_strat:
        apply_presets(main_strat, m_type)
        st.session_state.last_strat = main_strat

    if st.session_state.active_filters:
        st.caption("Aktive Parameter:")
        for n, v in list(st.session_state.active_filters.items()):
            c1, c2 = st.columns([5, 1])
            c1.write(f"**{n}:** {v[0]}-{v[1]}")
            if c2.button("×", key=f"del_{n}"):
                del st.session_state.active_filters[n]
                st.rerun()

    st.divider()
    st.subheader("⚙️ Feinjustierung")
    f_type = st.selectbox("Indikator", ["Kursänderung %", "Volumen", "Preis min-max", "RVOL", "SMA Trend"])
    if f_type == "RVOL": val = st.slider("RVOL", 0.0, 50.0, (1.5, 5.0), key=f"sl_{f_type}")
    elif f_type == "SMA Trend": val = st.slider("SMA Trend %", -20.0, 20.0, (0.5, 3.0), key=f"sl_{f_type}")
    else: val = st.slider("Bereich", -100.0, 100.0, (0.0, 10.0), key=f"sl_{f_type}")
    if st.button("➕ Hinzufügen"):
        st.session_state.active_filters[f_type] = val
        st.rerun()

    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        with st.status("Verbinde mit Polygon API...") as status:
            poly_key = st.secrets["POLYGON_KEY"]
            url = f"https://api.polygon.io/v2/snapshot/locale/global/markets/crypto/tickers?apiKey={poly_key}" if m_type == "Krypto" else f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
            try:
                resp = requests.get(url).json()
                res = []
                for t in resp.get("tickers", []):
                    # --- KRYPTO FIX: MULTI-LEVEL PRICE FETCH ---
                    day_d = t.get("day", {})
                    min_d = t.get("min", {})
                    trade_d = t.get("lastTrade", {})
                    
                    # Suche Preis in 5 Ebenen
                    price = day_d.get("c") or min_d.get("c") or trade_d.get("p") or t.get("todaysChangePerc", 0) #
                    if not price or price == 0: continue
                    
                    chg = t.get("todaysChangePerc") or 0
                    vol = day_d.get("v") or 1
                    prev = t.get("prevDay", {})
                    p_vol = prev.get("v") or (vol * 0.9)
                    p_close = prev.get("c") or price
                    
                    rvol = round(vol / p_vol, 2)
                    sma_trend = round(((price - p_close) / p_close) * 100, 2) if p_close != 0 else 0
                    
                    match = True
                    f = st.session_state.active_filters
                    if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                    if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                    if "Preis min-max" in f and not (f["Preis min-max"][0] <= price <= f["Preis min-max"][1]): match = False

                    if match:
                        res.append({"Ticker": t.get("ticker").replace("X:", ""), "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "Alpha-Score": calculate_alpha_score(rvol, sma_trend, chg)})
                
                st.session_state.scan_results = sorted(res, key=lambda x: x['Alpha-Score'], reverse=True)
                
                # Miroslavs Warnung [cite: 2025-12-28]
                if len(st.session_state.scan_results) < 30:
                    st.warning("Hey, ich habe leider keine 30 Spiele gefunden, aber hier sind trotzdem meine Empfehlungen. [cite: 2025-12-28]")
                
                status.update(label=f"Scan fertig: {len(st.session_state.scan_results)} Ergebnisse", state="complete")
            except Exception as e: st.error(f"API Fehler: {e}")

# TABS
t1, t2, t3 = st.tabs(["🚀 Trading Terminal", "📅 Wirtschaftskalender", "📊 Sektoren-Matrix"])
with t1:
    c_chart, c_journal = st.columns([2, 1])
    with c_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df_res = pd.DataFrame(st.session_state.scan_results)
            sel = st.dataframe(df_res, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
            if sel.selection and sel.selection.rows: st.session_state.selected_symbol = df_res.iloc[sel.selection.rows[0]]["Ticker"]
    with c_chart:
        st.subheader(f"📊 Chart: {st.session_state.selected_symbol}")
        tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if m_type == "Krypto" else st.session_state.selected_symbol
        st.components.v1.html(f'<div style="height:750px;width:100%"><div id="tv" style="height:100%"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize": true, "symbol": "{tv_sym}", "interval": "5", "theme": "dark", "style": "1", "locale": "de", "container_id": "tv", "withdateranges": true, "allow_symbol_change": true, "hide_side_toolbar": false}});</script></div>', height=750)

st.divider()
# --- KI FIX: TRY-EXCEPT FÜR NOTFOUND ERROR ---
if st.button("🤖 KI ANALYSE"):
    with st.spinner("Gemini analysiert..."):
        try:
            genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
            # Wir nutzen den vollqualifizierten Modellnamen
            model = genai.GenerativeModel('models/gemini-1.5-flash')
            response = model.generate_content(f"Analysiere {st.session_state.selected_symbol}. Gib KI-Rating 1-100 basierend auf Preis und Volumen. [cite: 2025-12-30]")
            st.info(response.text)
        except Exception as e:
            st.error(f"KI Fehler: {e}. Prüfe API-Key oder Modell-Verfügbarkeit.")

st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")