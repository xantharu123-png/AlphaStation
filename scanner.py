import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

# --- 1. INITIALISIERUNG (Session State) ---
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "SPY"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

# --- 2. MATHEMATISCHE STRATEGIE-DEFINITIONEN ---
def apply_presets(strat_name):
    """Definiert die mathematischen Leitplanken für jede der 13 Strategien"""
    presets = {
        "Volume Surge": {"RVOL": (2.0, 50.0), "Kursänderung %": (0.5, 30.0)},
        "Gap Momentum": {"Gap %": (2.5, 25.0), "RVOL": (1.2, 50.0)},
        "Penny Stock Breakout": {"Preis min-max": (0.5, 5.0), "Volumen": (1000000, 50000000000)},
        "Bull Flag Breakout": {"Vortag %": (4.0, 20.0), "Kursänderung %": (-1.0, 2.0)},
        "Unusual Volume": {"RVOL": (5.0, 100.0), "Volumen": (2000000, 50000000000)},
        "High of Day (HOD)": {"Kursänderung %": (1.0, 25.0), "RVOL": (1.3, 50.0)},
        "Short Squeeze Candidate": {"Kursänderung %": (8.0, 45.0), "RVOL": (3.0, 50.0)},
        "Low Float Flyer": {"Preis min-max": (1.0, 12.0), "Kursänderung %": (12.0, 100.0)},
        "Blue Chip Pullback": {"Preis min-max": (50.0, 3000.0), "Kursänderung %": (-4.0, -0.2)},
        "Multi-Day Runner": {"Vortag %": (2.0, 15.0), "Kursänderung %": (2.0, 15.0)},
        "Pre-Market Gapper": {"Gap %": (4.0, 40.0), "Volumen": (100000, 50000000000)},
        "Dead Cat Bounce": {"Vortag %": (-40.0, -9.0), "Kursänderung %": (1.5, 12.0)},
        "Golden Cross Proxy": {"Kursänderung %": (0.5, 10.0), "RVOL": (1.1, 10.0)}
    }
    if strat_name in presets:
        st.session_state.active_filters = presets[strat_name].copy()

# --- 3. HELPER FUNKTIONEN (KI, Daten, Login) ---

def get_ticker_news(ticker, poly_key):
    url = f"https://api.polygon.io/v2/reference/news?ticker={ticker}&limit=5&apiKey={poly_key}"
    try:
        resp = requests.get(url).json()
        return "\n".join([n.get("title", "") for n in resp.get("results", [])])
    except: return "Keine News verfügbar."

def get_gemini_response(prompt):
    """Robustes KI-Modell-Handling für 2025"""
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    models = ["gemini-2.0-flash", "gemini-1.5-flash"]
    for m in models:
        try:
            model = genai.GenerativeModel(m)
            return model.generate_content(prompt).text
        except: continue
    return "KI-Modell-Fehler. API-Status prüfen."

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login"):
            pw = st.text_input("Passwort", type="password")
            if st.form_submit_button("Anmelden"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
                else: st.error("Passwort inkorrekt.")
        return False
    return True

# --- 4. HAUPTPROGRAMM ---
if check_password():
    st.set_page_config(page_title="Alpha V33 Master", layout="wide")

    with st.sidebar:
        st.title("💎 Alpha V33 Master")
        
        # Extended Hours Haken
        ext_hours = st.checkbox("Pre & Post Market einbeziehen", value=True)
        
        st.divider()
        strat_list = ["Volume Surge", "Gap Momentum", "Penny Stock Breakout", "Bull Flag Breakout", "Unusual Volume", "High of Day (HOD)", "Short Squeeze Candidate", "Low Float Flyer", "Blue Chip Pullback", "Multi-Day Runner", "Pre-Market Gapper", "Dead Cat Bounce", "Golden Cross Proxy"]
        main_strat = st.selectbox("Basis-Strategie wählen", strat_list)
        
        if main_strat != st.session_state.last_strat:
            apply_presets(main_strat)
            st.session_state.last_strat = main_strat

        # Anzeige Aktive Filter (Rezept)
        if st.session_state.active_filters:
            st.caption("Aktive Rezept-Filter:")
            for n, v in list(st.session_state.active_filters.items()):
                c1, c2 = st.columns([5, 1])
                c1.write(f"**{n}:** {v[0]}-{v[1]}")
                if c2.button("×", key=f"d_{n}"):
                    del st.session_state.active_filters[n]
                    st.rerun()

        st.divider()
        # Feinjustierung
        f_type = st.selectbox("Filter hinzufügen", ["Kursänderung %", "Volumen", "Preis min-max", "RVOL", "Gap %"])
        if f_type == "RVOL": val = st.slider("RVOL", 0.0, 20.0, (1.5, 5.0))
        elif f_type == "Volumen": val = st.slider("Volumen", 0, 100000000, (500000, 5000000))
        else: val = st.slider("Bereich", -50.0, 100.0, (0.0, 10.0))
        
        if st.button("➕ Zum Rezept hinzufügen"):
            st.session_state.active_filters[f_type] = val
            st.rerun()

        st.divider()
        if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
            with st.status("Analysiere Markt-Mathematik...") as status:
                poly_key = st.secrets["POLYGON_KEY"]
                url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
                try:
                    resp = requests.get(url).json()
                    res = []
                    for t in resp.get("tickers", []):
                        # Daten-Mapping (Pre/Post Berücksichtigung)
                        ticker_data = t.get("fm", t.get("min", {})) if ext_hours else t.get("min", {})
                        price = ticker_data.get("c", 0)
                        sym = t.get("ticker")
                        chg = t.get("todaysChangePerc", 0)
                        vol = t.get("day", {}).get("v", 1)
                        high = t.get("day", {}).get("h", 1)
                        open_p = t.get("day", {}).get("o", 1)
                        
                        prev = t.get("prevDay", {})
                        p_close, p_vol, p_open = prev.get("c", 1), prev.get("v", 1), prev.get("o", 1)

                        # Mathematische Berechnungen
                        rvol = round(vol / p_vol, 2) if p_vol > 0 else 0
                        gap = round(((open_p - p_close) / p_close) * 100, 2)
                        p_perf = round(((p_close - p_open) / p_open) * 100, 2)
                        dist_hod = ((high - price) / high) * 100 if high > 0 else 100

                        # Strategie-Check (Exakte Mathematik)
                        match = True
                        if main_strat == "High of Day (HOD)" and dist_hod > 0.5: match = False
                        elif main_strat == "Dead Cat Bounce" and not (p_perf < -8 and chg > 1): match = False
                        elif main_strat == "Multi-Day Runner" and not (p_perf > 1.8 and chg > 1.8): match = False
                        elif main_strat == "Bull Flag Breakout" and not (p_perf > 3.5 and -0.5 <= chg <= 2): match = False
                        elif main_strat == "Gap Momentum" and abs(gap) < 2.5: match = False

                        # Filter-Check
                        f = st.session_state.active_filters
                        if match:
                            if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                            if "Gap %" in f and not (f["Gap %"][0] <= gap <= f["Gap %"][1]): match = False
                            if "Vortag %" in f and not (f["Vortag %"][0] <= p_perf <= f["Vortag %"][1]): match = False
                            if "Preis min-max" in f and not (f["Preis min-max"][0] <= price <= f["Preis min-max"][1]): match = False
                            if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                            if "Volumen" in f and not (f["Volumen"][0] <= vol <= f["Volumen"][1]): match = False

                        if match and price > 0:
                            res.append({"Ticker": sym, "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "Gap%": gap})
                    
                    st.session_state.scan_results = sorted(res, key=lambda x: x['Chg%'], reverse=True)
                    if len(st.session_state.scan_results) < 30:
                        st.warning("Hey, ich habe leider keine 30 Spiele gefunden, aber hier sind trotzdem meine Empfehlungen.")
                    status.update(label=f"Scan fertig: {len(res)} Signale", state="complete")
                except: st.error("API Verbindung fehlgeschlagen.")

        # Suche & Favoriten
        st.divider()
        search_ticker = st.text_input("Ticker Suche", "").upper()
        if st.button("TICKER LADEN"):
             st.session_state.selected_symbol = search_ticker
        if st.button("⭐ IN WATCHLIST"):
            if st.session_state.selected_symbol not in st.session_state.watchlist:
                st.session_state.watchlist.append(st.session_state.selected_symbol)

    # --- 5. HAUPTBEREICH (JOURNAL & CHART) ---
    c_chart, c_journal = st.columns([1.6, 1])
    
    with c_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            # KEYBOARD NAVIGATION: Pfeiltasten + Enter zum Auswählen
            selection = st.dataframe(df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
            if selection.selection and selection.selection.rows:
                selected_index = selection.selection.rows[0]
                new_sym = df.iloc[selected_index]["Ticker"]
                if new_sym != st.session_state.selected_symbol:
                    st.session_state.selected_symbol = new_sym
                    st.rerun()
        else: st.info("Bitte Scan starten.")

    with c_chart:
        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="550" frameborder="0"></iframe>', height=550)

    # --- 6. KI ANALYSE & DAILY REPORT ---
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button(f"🤖 KI ANALYSE: {st.session_state.selected_symbol}"):
            with st.spinner("Gemini analysiert..."):
                news = get_ticker_news(st.session_state.selected_symbol, st.secrets["POLYGON_KEY"])
                prompt = f"Analysiere {st.session_state.selected_symbol} für Miroslav. Berücksichtige Sektoren-Rotation und News-Sentiment. News: {news}."
                st.info(get_gemini_response(prompt))
    with c2:
        if st.button("📊 VOLLSTÄNDIGER MARKT-REPORT"):
            if st.session_state.scan_results:
                with st.spinner("Erstelle Report..."):
                    summary = pd.DataFrame(st.session_state.scan_results).head(10).to_string()
                    report = get_gemini_response(f"Erstelle Marktanalyse für heute {datetime.now()}. Fokus auf Sektoren & RVOL-Spikes. Daten: {summary}")
                    st.markdown(f"### 📅 Report vom {datetime.now().strftime('%d.%m.%Y')}\n{report}")
            else: st.warning("Keine Daten vorhanden.")

    st.caption(f"⚙️ Admin: Miroslav | Stand: {datetime.now().strftime('%H:%M:%S')}")