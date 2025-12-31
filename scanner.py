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
        "Golden Cross Proxy": {"Kursänderung %": (0.5, 10.0), "RVOL": (1.1, 10.0)}
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
    """KI-Failsafe für Modell-Versionen"""
    try:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        # Wir probieren die stabilsten Modelle für Dez 2025
        for m_name in ["gemini-2.0-flash", "gemini-1.5-flash"]:
            try:
                model = genai.GenerativeModel(m_name)
                return model.generate_content(prompt).text
            except: continue
        return "Modell-Fehler."
    except: return "KI im Standby."

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login_form"):
            pw = st.text_input("Admin Passwort", type="password")
            if st.form_submit_button("Einloggen"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
                else: st.error("Passwort falsch.")
        return False
    return True

# --- 4. HAUPTPROGRAMM ---

if check_password():
    st.set_page_config(page_title="Alpha V33 Master Multi", layout="wide")

    with st.sidebar:
        st.title("💎 Alpha V33 Master")
        
        # Marktauswahl
        market_type = st.radio("Märkte:", ["Aktien", "Krypto"], horizontal=True)
        
        if market_type == "Aktien":
            ext_hours = st.checkbox("Pre & Post Market", value=True)
            poly_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        else:
            ext_hours = False # Krypto ist 24/7
            poly_url = "https://api.polygon.io/v2/snapshot/locale/global/markets/crypto/tickers"
        
        st.divider()
        
        # Strategien
        strat_list = ["Volume Surge", "Gap Momentum", "Penny Stock/Moon Shot", "Bull Flag Breakout", "Unusual Volume", "High of Day (HOD)", "Short Squeeze Candidate", "Low Float/Market Cap", "Blue Chip Pullback", "Multi-Day Runner", "Pre-Market Gapper", "Dead Cat Bounce", "Golden Cross Proxy"]
        main_strat = st.selectbox("Strategie-Rezept", strat_list)
        
        if main_strat != st.session_state.last_strat:
            apply_presets(main_strat, market_type)
            st.session_state.last_strat = main_strat

        # Aktive Filter Anzeige & Löschen (Das "X")
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
        f_options = ["Kursänderung %", "Volumen", "Preis min-max", "RVOL", "Gap %", "Market Cap (Mrd $)", "Abstand vom Hoch %"]
        f_type = st.selectbox("Filter wählen", f_options)
        
        if f_type == "RVOL": val = st.slider("RVOL", 0.0, 20.0, (1.5, 5.0))
        elif f_type == "Volumen": val = st.slider("Volumen", 0, 100000000, (500000, 5000000))
        elif f_type == "Market Cap (Mrd $)": val = st.slider("Market Cap (Mrd $)", 0.0, 3000.0, (0.0, 500.0))
        elif f_type == "Abstand vom Hoch %": val = st.slider("Abstand vom Hoch %", 0.0, 10.0, (0.0, 1.0))
        else: val = st.slider("Bereich", -50.0, 100.0, (0.0, 10.0))
        
        if st.button("➕ Hinzufügen"):
            st.session_state.active_filters[f_type] = val
            st.rerun()

        st.divider()
        if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
            with st.status(f"Analysiere {market_type} Markt-Mathematik...") as status:
                poly_key = st.secrets["POLYGON_KEY"]
                try:
                    resp = requests.get(f"{poly_url}?apiKey={poly_key}").json()
                    res = []
                    for t in resp.get("tickers", []):
                        # --- Daten-Extraktion ---
                        raw_ticker = t.get("ticker")
                        clean_ticker = raw_ticker.replace("X:", "") if market_type == "Krypto" else raw_ticker
                        
                        ticker_data = t.get("fm", t.get("min", {})) if ext_hours else t.get("min", {})
                        price = ticker_data.get("c", t.get("lastTrade", {}).get("p", 0))
                        
                        chg = t.get("todaysChangePerc", 0)
                        vol = t.get("day", {}).get("v", 1)
                        high = t.get("day", {}).get("h", 1)
                        open_p = t.get("day", {}).get("o", 1)
                        m_cap = t.get("market_cap", 0) / 1_000_000_000 if t.get("market_cap") else 0
                        
                        prev = t.get("prevDay", {})
                        p_close, p_vol, p_open = prev.get("c", 1), prev.get("v", 1), prev.get("o", 1)

                        # --- Echte Mathematische Berechnungen ---
                        rvol = round(vol / p_vol, 2) if p_vol > 0 else 0
                        gap = round(((open_p - p_close) / p_close) * 100, 2)
                        p_perf = round(((p_close - p_open) / p_open) * 100, 2)
                        dist_hod = round(((high - price) / high) * 100, 2) if high > 0 else 100

                        # --- Strategie-Check (Exakte Logik) ---
                        match = True
                        if main_strat == "High of Day (HOD)" and dist_hod > 0.5: match = False
                        elif main_strat == "Dead Cat Bounce" and not (p_perf < -8 and chg > 1): match = False
                        elif main_strat == "Multi-Day Runner" and not (p_perf > 1.8 and chg > 1.8): match = False
                        elif main_strat == "Bull Flag Breakout" and not (p_perf > 3.5 and -0.5 <= chg <= 2): match = False
                        elif main_strat == "Gap Momentum" and abs(gap) < 2.5: match = False

                        # --- Additiver Filter-Check ---
                        f = st.session_state.active_filters
                        if match:
                            if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                            if "Gap %" in f and not (f["Gap %"][0] <= gap <= f["Gap %"][1]): match = False
                            if "Vortag %" in f and not (f["Vortag %"][0] <= p_perf <= f["Vortag %"][1]): match = False
                            if "Preis min-max" in f and not (f["Preis min-max"][0] <= price <= f["Preis min-max"][1]): match = False
                            if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                            if "Volumen" in f and not (f["Volumen"][0] <= vol <= f["Volumen"][1]): match = False
                            if "Market Cap (Mrd $)" in f and not (f["Market Cap (Mrd $)"][0] <= m_cap <= f["Market Cap (Mrd $)"][1]): match = False
                            if "Abstand vom Hoch %" in f and not (f["Abstand vom Hoch %"][0] <= dist_hod <= f["Abstand vom Hoch %"][1]): match = False

                        if match and price > 0:
                            res.append({"Ticker": clean_ticker, "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "Gap%": gap, "Cap(B)": round(m_cap, 2)})
                    
                    st.session_state.scan_results = sorted(res, key=lambda x: x['Chg%'], reverse=True)
                    if len(st.session_state.scan_results) < 30:
                        st.warning("Hey, ich habe leider keine 30 Spiele gefunden, aber hier sind trotzdem meine Empfehlungen.")
                    status.update(label=f"Analyse fertig: {len(res)} Signale", state="complete")
                except Exception as e: st.error(f"API Fehler: {e}")

        # Suche & Favoriten
        st.divider()
        search_ticker = st.text_input("Ticker Einzelsuche", "").upper()
        if st.button("LADEN"): st.session_state.selected_symbol = search_ticker
        if st.button("⭐ WATCHLIST"):
            if st.session_state.selected_symbol not in st.session_state.watchlist: 
                st.session_state.watchlist.append(st.session_state.selected_symbol)

    # --- 5. HAUPTBEREICH (JOURNAL & CHART) ---
    c_chart, c_journal = st.columns([1.6, 1])
    
    with c_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            # KEYBOARD NAVIGATION: Pfeiltasten + Enter
            selection = st.dataframe(df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
            if selection.selection and selection.selection.rows:
                selected_index = selection.selection.rows[0]
                new_sym = df.iloc[selected_index]["Ticker"]
                if new_sym != st.session_state.selected_symbol:
                    st.session_state.selected_symbol = new_sym
                    st.rerun()
        else: st.info(f"Scanner bereit für Miroslav.")

    with c_chart:
        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        # Chart-Präfix Anpassung (Aktien vs. Krypto)
        if market_type == "Krypto":
            # TradingView Logik für Krypto
            tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if "USD" not in st.session_state.selected_symbol else st.session_state.selected_symbol
        else:
            tv_sym = st.session_state.selected_symbol
            
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={tv_sym}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="550" frameborder="0"></iframe>', height=550)

    # --- 6. KI ANALYSE ---
    st.divider()
    if st.button(f"🤖 GEMINI ANALYSE: {st.session_state.selected_symbol}"):
        with st.spinner("Gemini analysiert Markt-Sentiment..."):
            news = get_ticker_news(st.session_state.selected_symbol, st.secrets["POLYGON_KEY"])
            prompt = f"Analysiere {st.session_state.selected_symbol} ({market_type}) für Miroslav. Nutze Sektoren-Rotation und News-Sentiment. News: {news}."
            st.info(get_gemini_response(prompt))

    st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")