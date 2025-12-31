import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

# --- 1. INITIALISIERUNG (Session State) ---
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "AAPL"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

# --- 2. MATHEMATISCHE STRATEGIE-DEFINITIONEN ---
def apply_presets(strat_name, market_type):
    """Setzt die mathematischen Grenzwerte für jede Strategie"""
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

# --- 3. HELPER FUNKTIONEN ---
def get_ticker_news(ticker, poly_key):
    url = f"https://api.polygon.io/v2/reference/news?ticker={ticker}&limit=5&apiKey={poly_key}"
    try:
        resp = requests.get(url).json()
        return "\n".join([n.get("title", "") for n in resp.get("results", [])])
    except: return "Keine News verfügbar."

def get_gemini_response(prompt):
    try:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-1.5-flash')
        return model.generate_content(prompt).text
    except: return "KI im Standby."

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login"):
            pw = st.text_input("Admin Passwort", type="password")
            if st.form_submit_button("Einloggen"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
        return False
    return True

# --- 4. HAUPTPROGRAMM ---
if check_password():
    st.set_page_config(page_title="Alpha V33 Master Pro", layout="wide")

    with st.sidebar:
        st.title("💎 Alpha V33 Master")
        market_type = st.radio("Märkte:", ["Aktien", "Krypto"], horizontal=True)
        
        if market_type == "Aktien":
            ext_hours = st.checkbox("Pre & Post Market", value=True)
            poly_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        else:
            ext_hours = False
            poly_url = "https://api.polygon.io/v2/snapshot/locale/global/markets/crypto/tickers"
        
        st.divider()
        strat_list = ["Volume Surge", "Gap Momentum", "Penny Stock/Moon Shot", "Bull Flag Breakout", "Unusual Volume", "High of Day (HOD)", "Short Squeeze Candidate", "Low Float/Market Cap", "Blue Chip Pullback", "Multi-Day Runner", "Pre-Market Gapper", "Dead Cat Bounce", "Golden Cross Proxy"]
        main_strat = st.selectbox("Strategie-Rezept", strat_list)
        
        if main_strat != st.session_state.last_strat:
            apply_presets(main_strat, market_type)
            st.session_state.last_strat = main_strat

        # Aktive Parameter Anzeige & Löschen
        if st.session_state.active_filters:
            st.caption("Aktive Parameter:")
            for n, v in list(st.session_state.active_filters.items()):
                c1, c2 = st.columns([5, 1])
                c1.write(f"**{n}:** {v[0]}-{v[1]}")
                if c2.button("×", key=f"del_{n}"):
                    del st.session_state.active_filters[n]
                    st.rerun()

        st.divider()
        # --- FIX: FEINJUSTIERUNG MIT DYNAMISCHEM SLIDER ---
        st.subheader("⚙️ Feinjustierung")
        f_options = ["Kursänderung %", "Volumen", "Preis min-max", "RVOL", "Gap %", "Market Cap (Mrd $)", "Abstand vom Hoch %", "SMA Trend"]
        f_type = st.selectbox("Indikator/Filter", f_options)
        
        # Jeder Slider bekommt einen eindeutigen Key basierend auf f_type
        if f_type == "RVOL": 
            val = st.slider("RVOL Bereich", 0.0, 50.0, (1.5, 5.0), key=f"sl_{f_type}")
        elif f_type == "SMA Trend": 
            val = st.slider("SMA Abstand %", -20.0, 20.0, (0.5, 3.0), key=f"sl_{f_type}")
        elif f_type == "Market Cap (Mrd $)": 
            val = st.slider("Cap (Mrd $)", 0.0, 3500.0, (0.0, 500.0), key=f"sl_{f_type}")
        elif f_type == "Volumen": 
            val = st.slider("Volumen", 0, 100000000, (500000, 5000000), key=f"sl_{f_type}")
        elif f_type == "Preis min-max":
            val = st.slider("Preis Bereich", 0.0, 5000.0, (1.0, 100.0), key=f"sl_{f_type}")
        elif f_type == "Gap %":
            val = st.slider("Gap Bereich %", -50.0, 50.0, (2.0, 10.0), key=f"sl_{f_type}")
        elif f_type == "Abstand vom Hoch %":
            val = st.slider("HOD Abstand %", 0.0, 20.0, (0.0, 1.0), key=f"sl_{f_type}")
        else: # Kursänderung %
            val = st.slider("Änderung %", -100.0, 100.0, (0.0, 10.0), key=f"sl_{f_type}")
        
        if st.button("➕ Hinzufügen", use_container_width=True):
            st.session_state.active_filters[f_type] = val
            st.rerun()

        st.divider()
        if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
            with st.status(f"Analysiere {market_type}...") as status:
                poly_key = st.secrets["POLYGON_KEY"]
                try:
                    resp = requests.get(f"{poly_url}?apiKey={poly_key}").json()
                    res = []
                    for t in resp.get("tickers", []):
                        raw_ticker = t.get("ticker")
                        clean_ticker = raw_ticker.replace("X:", "") if market_type == "Krypto" else raw_ticker
                        ticker_data = t.get("fm", t.get("min", {})) if ext_hours else t.get("min", {})
                        price = ticker_data.get("c", t.get("lastTrade", {}).get("p", 0))
                        chg, vol, high, open_p = t.get("todaysChangePerc", 0), t.get("day", {}).get("v", 1), t.get("day", {}).get("h", 1), t.get("day", {}).get("o", 1)
                        m_cap = t.get("market_cap", 0) / 1_000_000_000 if t.get("market_cap") else 0
                        prev = t.get("prevDay", {})
                        p_close, p_high, p_low = prev.get("c", 1), prev.get("h", 1), prev.get("l", 1)

                        rvol = round(vol / prev.get("v", 1), 2) if prev.get("v", 0) > 0 else 0
                        gap = round(((open_p - p_close) / p_close) * 100, 2)
                        p_perf = round(((p_close - prev.get("o", 1)) / prev.get("o", 1)) * 100, 2)
                        dist_hod = round(((high - price) / high) * 100, 2) if high > 0 else 100
                        sma_trend = round(((price - ((p_high + p_low + p_close) / 3)) / ((p_high + p_low + p_close) / 3)) * 100, 2)

                        match = True
                        if main_strat == "High of Day (HOD)" and dist_hod > 0.5: match = False
                        elif main_strat == "Dead Cat Bounce" and not (p_perf < -8 and chg > 1): match = False

                        f = st.session_state.active_filters
                        if match:
                            if "SMA Trend" in f and not (f["SMA Trend"][0] <= sma_trend <= f["SMA Trend"][1]): match = False
                            if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                            if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                            if "Preis min-max" in f and not (f["Preis min-max"][0] <= price <= f["Preis min-max"][1]): match = False
                            if "Market Cap (Mrd $)" in f and not (f["Market Cap (Mrd $)"][0] <= m_cap <= f["Market Cap (Mrd $)"][1]): match = False

                        if match and price > 0:
                            res.append({"Ticker": clean_ticker, "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "SMA%": sma_trend, "Cap(B)": round(m_cap, 2)})
                    
                    st.session_state.scan_results = sorted(res, key=lambda x: x['Chg%'], reverse=True)
                    if len(st.session_state.scan_results) < 30:
                        st.warning("Hey, ich habe leider keine 30 Spiele gefunden, aber hier sind trotzdem meine Empfehlungen.")
                    status.update(label=f"Scan fertig: {len(res)} Signale", state="complete")
                except Exception as e: st.error(f"Fehler: {e}")

        st.divider()
        st.subheader("🔍 Suche & Favoriten")
        search_ticker = st.text_input("Ticker Suche", "").upper()
        if st.button("TICKER LADEN", use_container_width=True): st.session_state.selected_symbol = search_ticker
        if st.button("⭐ IN WATCHLIST", use_container_width=True):
            if st.session_state.selected_symbol not in st.session_state.watchlist:
                st.session_state.watchlist.append(st.session_state.selected_symbol)
        if st.session_state.watchlist:
            for w_sym in st.session_state.watchlist:
                wc1, wc2 = st.columns([4, 1])
                if wc1.button(w_sym, key=f"ws_{w_sym}"): st.session_state.selected_symbol = w_sym
                if wc2.button("×", key=f"wd_{w_sym}"): 
                    st.session_state.watchlist.remove(w_sym)
                    st.rerun()

    # --- 5. HAUPTBEREICH (Tabs) ---
    tab_terminal, tab_calendar = st.tabs(["🚀 Trading Terminal", "📅 Wirtschaftskalender"])

    with tab_terminal:
        c_chart, c_journal = st.columns([2, 1])
        with c_journal:
            st.subheader("📝 Signal Journal")
            if st.session_state.scan_results:
                df = pd.DataFrame(st.session_state.scan_results)
                # NAVIGATION: Pfeiltasten + Enter
                selection = st.dataframe(df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
                if selection.selection and selection.selection.rows:
                    st.session_state.selected_symbol = df.iloc[selection.selection.rows[0]]["Ticker"]
            else: st.info("Bereit für Scan.")

        with c_chart:
            st.subheader(f"📊 Chart: {st.session_state.selected_symbol}")
            tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if market_type == "Krypto" else st.session_state.selected_symbol
            tradingview_html = f"""
            <div class="tradingview-widget-container" style="height:750px;width:100%">
              <div id="tradingview_pro" style="height:100%;width:100%"></div>
              <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
              <script type="text/javascript">
              new TradingView.widget({{ 
                "autosize": true, "symbol": "{tv_sym}", "interval": "5", "timezone": "Etc/UTC", "theme": "dark", "style": "1", "locale": "de", 
                "hide_side_toolbar": false, "withdateranges": true, "allow_symbol_change": true, "container_id": "tradingview_pro" 
              }});
              </script>
            </div> """
            st.components.v1.html(tradingview_html, height=750)

    with tab_calendar:
        st.components.v1.html('<iframe src="https://www.tradingview.com/embed-widget/events/?locale=de" width="100%" height="750" frameborder="0"></iframe>', height=800)

    # KI ANALYSE
    st.divider()
    if st.button(f"🤖 KI ANALYSE: {st.session_state.selected_symbol}"):
        with st.spinner("KI berechnet Sentiment..."):
            news = get_ticker_news(st.session_state.selected_symbol, st.secrets["POLYGON_KEY"])
            st.info(get_gemini_response(f"Analysiere {st.session_state.selected_symbol}. News: {news}"))

    st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")