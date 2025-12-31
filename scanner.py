import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

# --- 1. INITIALISIERUNG ---
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "AAPL"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

# --- 2. MATHEMATISCHE STRATEGIE-DEFINITIONEN ---
def apply_presets(strat_name, market_type):
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
        f_options = ["Kursänderung %", "Volumen", "Preis min-max", "RVOL", "Gap %", "Market Cap (Mrd $)", "Abstand vom Hoch %", "SMA Trend"]
        f_type = st.selectbox("Indikator/Filter", f_options)
        
        if f_type == "RVOL": val = st.slider("RVOL", 0.0, 20.0, (1.5, 5.0))
        elif f_type == "SMA Trend": val = st.slider("SMA Abstand % (Preis vs Durchschnitt)", -10.0, 10.0, (0.5, 3.0))
        elif f_type == "Market Cap (Mrd $)": val = st.slider("Cap (Mrd $)", 0.0, 3000.0, (0.0, 500.0))
        else: val = st.slider("Bereich", -50.0, 100.0, (0.0, 10.0))
        
        if st.button("➕ Hinzufügen"):
            st.session_state.active_filters[f_type] = val
            st.rerun()

        st.divider()
        if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
            with st.status(f"Analysiere {market_type} Mathematik...") as status:
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
                        p_close, p_vol, p_open, p_high, p_low = prev.get("c", 1), prev.get("v", 1), prev.get("o", 1), prev.get("h", 1), prev.get("l", 1)

                        # MATHEMATIK (Indikatoren)
                        rvol = round(vol / p_vol, 2) if p_vol > 0 else 0
                        gap = round(((open_p - p_close) / p_close) * 100, 2)
                        p_perf = round(((p_close - p_open) / p_open) * 100, 2)
                        dist_hod = round(((high - price) / high) * 100, 2) if high > 0 else 100
                        # SMA-Trend Approximation (Preis vs. Durchschnitt Vortag)
                        sma_approx = (p_high + p_low + p_close) / 3
                        sma_trend = round(((price - sma_approx) / sma_approx) * 100, 2)

                        match = True
                        if main_strat == "High of Day (HOD)" and dist_hod > 0.5: match = False
                        elif main_strat == "Dead Cat Bounce" and not (p_perf < -8 and chg > 1): match = False
                        elif main_strat == "Golden Cross Proxy" and sma_trend < 0.5: match = False

                        f = st.session_state.active_filters
                        if match:
                            if "SMA Trend" in f and not (f["SMA Trend"][0] <= sma_trend <= f["SMA Trend"][1]): match = False
                            if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                            if "Preis min-max" in f and not (f["Preis min-max"][0] <= price <= f["Preis min-max"][1]): match = False
                            if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                            if "Market Cap (Mrd $)" in f and not (f["Market Cap (Mrd $)"][0] <= m_cap <= f["Market Cap (Mrd $)"][1]): match = False

                        if match and price > 0:
                            res.append({"Ticker": clean_ticker, "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "SMA Dist%": sma_trend, "Cap(B)": round(m_cap, 2)})
                    
                    st.session_state.scan_results = sorted(res, key=lambda x: x['Chg%'], reverse=True)
                    if len(st.session_state.scan_results) < 30:
                        st.warning("Hey, ich habe leider keine 30 Spiele gefunden, aber hier sind trotzdem meine Empfehlungen.")
                    status.update(label=f"Analyse fertig: {len(res)} Signale", state="complete")
                except Exception as e: st.error(f"Fehler: {e}")

    # --- 5. HAUPTBEREICH (Tabs für Terminals) ---
    tab_terminal, tab_calendar = st.tabs(["🚀 Trading Terminal", "📅 Wirtschaftskalender"])

    with tab_terminal:
        c_chart, c_journal = st.columns([1.6, 1])
        with c_journal:
            st.subheader("📝 Signal Journal")
            if st.session_state.scan_results:
                df = pd.DataFrame(st.session_state.scan_results)
                selection = st.dataframe(df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
                if selection.selection and selection.selection.rows:
                    selected_index = selection.selection.rows[0]
                    st.session_state.selected_symbol = df.iloc[selected_index]["Ticker"]
            else: st.info("Bereit für Scan.")

        with c_chart:
            st.subheader(f"📊 Chart: {st.session_state.selected_symbol}")
            tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if market_type == "Krypto" else st.session_state.selected_symbol
            tradingview_html = f"""
            <div class="tradingview-widget-container" style="height:550px;width:100%">
              <div id="tradingview_pro"></div>
              <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
              <script type="text/javascript">
              new TradingView.widget({{ "autosize": true, "symbol": "{tv_sym}", "interval": "5", "timezone": "Etc/UTC", "theme": "dark", "style": "1", "locale": "de", "hide_side_toolbar": false, "container_id": "tradingview_pro" }});
              </script>
            </div> """
            st.components.v1.html(tradingview_html, height=550)

    with tab_calendar:
        st.subheader("🗓️ Globale Wirtschaftstermine")
        calendar_html = """
        <div class="tradingview-widget-container">
          <iframe src="https://www.tradingview.com/embed-widget/events/?locale=de#%7B%22colorTheme%22%3A%22dark%22%2C%22isTransparent%22%3Afalse%2C%22width%22%3A%22100%25%22%2C%22height%22%3A%22600%22%2C%22importanceFilter%22%3A%22-1%2C0%2C1%22%7D" width="100%" height="600" frameborder="0"></iframe>
        </div> """
        st.components.v1.html(calendar_html, height=650)

    # --- 6. KI ANALYSE ---
    st.divider()
    if st.button(f"🤖 KI ANALYSE: {st.session_state.selected_symbol}"):
        with st.spinner("KI berechnet Sentiment..."):
            prompt = f"Analysiere {st.session_state.selected_symbol} ({market_type}). Fokus auf Sektoren/Trends und News-Sentiment."
            st.info(get_gemini_response(prompt))

    st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")