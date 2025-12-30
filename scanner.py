import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

# --- 1. INITIALISIERUNG ---
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "SPY"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "manual_data" not in st.session_state: st.session_state.manual_data = None
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

# --- 2. SMART PRESETS LOGIK ---
def apply_presets(strat_name):
    presets = {
        "Volume Surge": {"Kursänderung %": (2.0, 15.0), "Volumen": (1000000, 50000000000)},
        "Gap Momentum": {"Kursänderung %": (3.0, 12.0), "Preis min-max": (5.0, 150.0)},
        "Penny Stock Breakout": {"Preis min-max": (0.5, 5.0), "Volumen": (1000000, 50000000000)},
        "Bull Flag Breakout": {"Kursänderung %": (2.0, 6.0), "Preis min-max": (10.0, 300.0)},
        "Unusual Volume": {"Volumen": (2000000, 50000000000), "Kursänderung %": (1.0, 20.0)},
        "High of Day (HOD)": {"Kursänderung %": (4.0, 10.0), "Volumen": (500000, 50000000000)},
        "Short Squeeze Candidate": {"Kursänderung %": (5.0, 25.0), "Volumen": (3000000, 50000000000)},
        "Low Float Flyer": {"Preis min-max": (1.0, 10.0), "Kursänderung %": (10.0, 50.0)},
        "Blue Chip Pullback": {"Preis min-max": (50.0, 1000.0), "Kursänderung %": (-3.0, -0.5)},
        "Multi-Day Runner": {"Kursänderung %": (5.0, 15.0), "Volumen": (1500000, 50000000000)},
        "Pre-Market Gapper": {"Kursänderung %": (4.0, 20.0), "Volumen": (100000, 50000000000)},
        "Dead Cat Bounce": {"Kursänderung %": (1.0, 5.0), "Preis min-max": (2.0, 50.0)},
        "Golden Cross Signal": {"Preis min-max": (15.0, 500.0), "Volumen": (1000000, 50000000000)}
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

def get_single_ticker_data(ticker, poly_key):
    url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey={poly_key}"
    try:
        data = requests.get(url).json()
        t = data["ticker"]
        return {"Ticker": t.get("ticker"), "Price": t.get("min", {}).get("c", 0), 
                "Chg%": round(t.get("todaysChangePerc", 0), 2), "Vol": int(t.get("day", {}).get("v", 0))}
    except: return None

def get_gemini_response(prompt):
    """Sicherer Modell-Aufruf für Ende 2025"""
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    # Liste der Modelle, die wir nacheinander probieren
    models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    
    for model_name in models_to_try:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            return response.text
        except:
            continue
    return "Fehler: Kein kompatibles Gemini-Modell gefunden. Bitte API-Key prüfen."

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login"):
            pw = st.text_input("Admin-Passwort", type="password")
            if st.form_submit_button("Anmelden"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
                else: st.error("Passwort falsch.")
        return False
    return True

# --- 4. HAUPTPROGRAMM ---
if check_password():
    st.set_page_config(page_title="Alpha V33 Master", layout="wide")

    with st.sidebar:
        st.title("💎 Alpha V33 Master")
        
        # A. REZEPT
        st.subheader("📋 Strategie-Rezept")
        strat_list = ["Volume Surge", "Gap Momentum", "Penny Stock Breakout", "Bull Flag Breakout", "Unusual Volume", "High of Day (HOD)", "Short Squeeze Candidate", "Low Float Flyer", "Blue Chip Pullback", "Multi-Day Runner", "Pre-Market Gapper", "Dead Cat Bounce", "Golden Cross Signal"]
        main_strat = st.selectbox("Wähle dein Fundament", strat_list)
        
        if main_strat != st.session_state.last_strat:
            apply_presets(main_strat)
            st.session_state.last_strat = main_strat

        if st.session_state.active_filters:
            st.caption("Aktive Parameter:")
            to_delete = []
            for name, v in st.session_state.active_filters.items():
                c_text, c_del = st.columns([5, 1])
                c_text.write(f"**{name}:** {v[0]} - {v[1]}")
                if c_del.button("×", key=f"del_{name}"): to_delete.append(name)
            for d in to_delete:
                del st.session_state.active_filters[d]
                st.rerun()
        
        st.divider()

        # B. ANPASSUNG
        st.subheader("⚙️ Feinjustierung")
        f_type = st.selectbox("Zusatz-Filter", ["Kursänderung %", "Volumen", "Preis min-max"])
        if f_type == "Kursänderung %": val = st.slider("Bereich %", -100.0, 100.0, (2.0, 15.0))
        elif f_type == "Volumen": val = st.slider("Volumen", 0, 50000000000, (1000000, 10000000000))
        else: val = st.slider("Preis $", 0.0, 1000.0, (1.0, 50.0))

        if st.button("➕ Hinzufügen"):
            st.session_state.active_filters[f_type] = val
            st.rerun()

        st.divider()
        if st.button("🚀 SCAN STARTEN", use_container_width=True, type="primary"):
            st.session_state.manual_data = None
            with st.status("Analysiere Markt...") as status:
                poly_key = st.secrets["POLYGON_KEY"]
                url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
                try:
                    resp = requests.get(url).json()
                    results = []
                    for t in resp.get("tickers", []):
                        sym, chg, vol, last = t.get("ticker"), t.get("todaysChangePerc", 0), t.get("day", {}).get("v", 0), t.get("min", {}).get("c", 0)
                        match = True
                        f = st.session_state.active_filters
                        if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                        if "Volumen" in f and not (f["Volumen"][0] <= vol <= f["Volumen"][1]): match = False
                        if "Preis min-max" in f and not (f["Preis min-max"][0] <= last <= f["Preis min-max"][1]): match = False
                        if match: results.append({"Ticker": sym, "Price": last, "Chg%": round(chg, 2), "Vol": int(vol)})
                    
                    st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                    if len(st.session_state.scan_results) < 30:
                        st.warning("Hey, ich habe leider keine 30 Treffer gefunden, aber hier sind trotzdem meine Empfehlungen.")
                    if st.session_state.scan_results: st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                    status.update(label="Scan erfolgreich!", state="complete")
                except: st.error("Fehler")

        # C. SUCHE & WATCHLIST
        st.divider()
        search_ticker = st.text_input("Einzelsuche", "").upper()
        if st.button("LADEN"):
            data = get_single_ticker_data(search_ticker, st.secrets["POLYGON_KEY"])
            if data: st.session_state.selected_symbol, st.session_state.manual_data = search_ticker, data
        if st.button("⭐ IN WATCHLIST"):
            if st.session_state.selected_symbol not in st.session_state.watchlist: st.session_state.watchlist.append(st.session_state.selected_symbol)

    # --- HAUPTBEREICH ---
    col_chart, col_journal = st.columns([1.5, 1])
    with col_chart:
        st.subheader(f"📊 Chart: {st.session_state.selected_symbol}")
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="520" frameborder="0"></iframe>', height=520)

    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            sel = st.dataframe(df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
            if sel.selection and sel.selection.rows:
                st.session_state.selected_symbol = df.iloc[sel.selection.rows[0]]["Ticker"]
        else: st.info("Scan starten...")

    # --- KI ANALYSE & REPORT ---
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button(f"🤖 GEMINI ANALYSE: {st.session_state.selected_symbol}"):
            with st.spinner("Analysiere..."):
                poly_key = st.secrets["POLYGON_KEY"]
                news = get_ticker_news(st.session_state.selected_symbol, poly_key)
                current = st.session_state.manual_data if st.session_state.manual_data else next((i for i in st.session_state.scan_results if i["Ticker"] == st.session_state.selected_symbol), {"Price":0, "Chg%":0})
                prompt = f"Analysiere {st.session_state.selected_symbol}. Kurs: ${current['Price']}, Chg: {current['Chg%']}%. News: {news}. Sentiment?"
                st.info(get_gemini_response(prompt))

    with c2:
        if st.button("📊 VOLLSTÄNDIGER KI MARKT-REPORT"):
            if st.session_state.scan_results:
                with st.spinner("Erstelle Report..."):
                    summary = pd.DataFrame(st.session_state.scan_results).head(10).to_string()
                    prompt = f"Erstelle Marktanalyse für heute {datetime.now()}. Fokus auf Sektoren & RVOL-Spikes. Daten: {summary}"
                    report = get_gemini_response(prompt)
                    st.markdown(f"### 📅 Report vom {datetime.now().strftime('%d.%m.%Y')}\n{report}")
            else: st.warning("Keine Daten.")

    st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")