import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

# --- 1. INITIALISIERUNG ---
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "SPY"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

# --- 2. STRATEGIE-LOGIK (Mathematisch definiert) ---
def apply_presets(strat_name):
    presets = {
        "Volume Surge": {"RVOL": (2.0, 50.0), "Kursänderung %": (1.0, 30.0)},
        "Gap Momentum": {"Gap %": (3.0, 25.0), "RVOL": (1.2, 50.0)},
        "Penny Stock Breakout": {"Preis min-max": (0.5, 5.0), "Volumen": (1000000, 50000000000)},
        "Bull Flag Breakout": {"Vortag %": (3.0, 20.0), "Kursänderung %": (0.0, 3.0)},
        "Unusual Volume": {"RVOL": (5.0, 100.0), "Volumen": (2000000, 50000000000)},
        "High of Day (HOD)": {"Kursänderung %": (2.0, 20.0), "RVOL": (1.5, 50.0)},
        "Short Squeeze Candidate": {"Kursänderung %": (8.0, 40.0), "RVOL": (3.0, 50.0)},
        "Low Float Flyer": {"Preis min-max": (1.0, 12.0), "Kursänderung %": (15.0, 100.0)},
        "Blue Chip Pullback": {"Preis min-max": (50.0, 2000.0), "Kursänderung %": (-5.0, -0.5)},
        "Multi-Day Runner": {"Vortag %": (2.0, 15.0), "Kursänderung %": (2.0, 15.0)},
        "Pre-Market Gapper": {"Gap %": (4.0, 40.0), "Volumen": (200000, 50000000000)},
        "Dead Cat Bounce": {"Vortag %": (-40.0, -8.0), "Kursänderung %": (1.5, 10.0)},
        "Golden Cross Proxy": {"Kursänderung %": (1.0, 10.0), "RVOL": (1.1, 10.0)}
    }
    if strat_name in presets:
        st.session_state.active_filters = presets[strat_name].copy()

# --- 3. HELPER ---
def get_gemini_response(prompt):
    try:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        # Wir nutzen das stabilste Modell für 2025
        model = genai.GenerativeModel('gemini-1.5-flash')
        return model.generate_content(prompt).text
    except: return "KI momentan im Standby."

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login"):
            pw = st.text_input("Passwort", type="password")
            if st.form_submit_button("Anmelden"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
        return False
    return True

# --- 4. SCANNER KERN ---
if check_password():
    st.set_page_config(page_title="Alpha V33 Master", layout="wide")

    with st.sidebar:
        st.title("💎 Alpha V33 Master")
        strat_list = list(pd.DataFrame([
            "Volume Surge", "Gap Momentum", "Penny Stock Breakout", "Bull Flag Breakout", "Unusual Volume", 
            "High of Day (HOD)", "Short Squeeze Candidate", "Low Float Flyer", "Blue Chip Pullback", 
            "Multi-Day Runner", "Pre-Market Gapper", "Dead Cat Bounce", "Golden Cross Proxy"
        ])[0])
        
        main_strat = st.selectbox("Strategie-Rezept", strat_list)
        if main_strat != st.session_state.last_strat:
            apply_presets(main_strat)
            st.session_state.last_strat = main_strat

        # Aktive Rezept-Filter
        if st.session_state.active_filters:
            st.caption("Aktive Parameter:")
            for n, v in st.session_state.active_filters.items():
                c1, c2 = st.columns([5, 1])
                c1.write(f"**{n}:** {v[0]}-{v[1]}")
                if c2.button("×", key=f"d_{n}"):
                    del st.session_state.active_filters[n]
                    st.rerun()

        st.divider()
        st.subheader("⚙️ Feinjustierung")
        f_type = st.selectbox("Zusatz-Filter", ["Kursänderung %", "Volumen", "Preis min-max", "RVOL", "Gap %"])
        if f_type == "RVOL": val = st.slider("RVOL", 0.0, 20.0, (1.5, 5.0))
        elif f_type == "Volumen": val = st.slider("Volumen", 0, 100000000, (500000, 5000000))
        else: val = st.slider("Bereich", -50.0, 100.0, (0.0, 10.0))
        
        if st.button("➕ Hinzufügen"):
            st.session_state.active_filters[f_type] = val
            st.rerun()

        st.divider()
        if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
            with st.status("Analysiere mathematische Modelle...") as status:
                poly_key = st.secrets["POLYGON_KEY"]
                url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
                try:
                    resp = requests.get(url).json()
                    res = []
                    for t in resp.get("tickers", []):
                        sym, price, chg, vol = t.get("ticker"), t.get("min", {}).get("c", 0), t.get("todaysChangePerc", 0), t.get("day", {}).get("v", 1)
                        high, open_p = t.get("day", {}).get("h", 1), t.get("day", {}).get("o", 1)
                        prev = t.get("prevDay", {})
                        p_close, p_vol, p_open = prev.get("c", 1), prev.get("v", 1), prev.get("o", 1)

                        # Formeln
                        rvol = round(vol / p_vol, 2) if p_vol > 0 else 0
                        gap = round(((open_p - p_close) / p_close) * 100, 2)
                        p_perf = round(((p_close - p_open) / p_open) * 100, 2)
                        dist_hod = ((high - price) / high) * 100 if high > 0 else 100

                        match = True
                        if main_strat == "High of Day (HOD)" and dist_hod > 0.3: match = False
                        elif main_strat == "Dead Cat Bounce" and not (p_perf < -8 and chg > 1.5): match = False
                        elif main_strat == "Multi-Day Runner" and not (p_perf > 2 and chg > 2): match = False
                        elif main_strat == "Bull Flag Breakout" and not (p_perf > 4 and 0 <= chg <= 2): match = False
                        elif main_strat == "Gap Momentum" and gap < 3: match = False

                        f = st.session_state.active_filters
                        if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                        if "Gap %" in f and not (f["Gap %"][0] <= gap <= f["Gap %"][1]): match = False
                        if "Vortag %" in f and not (f["Vortag %"][0] <= p_perf <= f["Vortag %"][1]): match = False
                        if "Preis min-max" in f and not (f["Preis min-max"][0] <= price <= f["Preis min-max"][1]): match = False
                        if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                        if "Volumen" in f and not (f["Volumen"][0] <= vol <= f["Volumen"][1]): match = False

                        if match: res.append({"Ticker": sym, "Price": price, "Chg%": chg, "RVOL": rvol, "Gap%": gap, "Vol": vol})
                    
                    st.session_state.scan_results = sorted(res, key=lambda x: x['Chg%'], reverse=True)
                    if len(st.session_state.scan_results) < 30:
                        st.warning("Hey, ich habe leider keine 30 Spiele gefunden, aber hier sind trotzdem meine Empfehlungen.")
                    status.update(label=f"Analyse fertig: {len(res)} Signale", state="complete")
                except: st.error("API Fehler")

    # --- HAUPTBEREICH (Journal & Chart) ---
    c_chart, c_journal = st.columns([1.6, 1])
    
    with c_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            # NEU: on_select="rerun" erlaubt Navigation mit Pfeiltasten + Enter
            selection = st.dataframe(
                df, 
                on_select="rerun", 
                selection_mode="single-row", 
                hide_index=True, 
                use_container_width=True
            )
            # Logik für die Auswahl
            if selection.selection and selection.selection.rows:
                selected_index = selection.selection.rows[0]
                st.session_state.selected_symbol = df.iloc[selected_index]["Ticker"]
        else:
            st.info("Scanner bereit für Miroslav.")

    with c_chart:
        st.subheader(f"📊 Chart: {st.session_state.selected_symbol}")
        # Wir nutzen ein iFrame für TradingView
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="550" frameborder="0"></iframe>', height=550)

    # --- KI ANALYSE ---
    st.divider()
    if st.button(f"🤖 GEMINI ANALYSE: {st.session_state.selected_symbol}"):
        with st.spinner("KI wertet aus..."):
            prompt = f"Daytrading Analyse für {st.session_state.selected_symbol}. Daten: {st.session_state.scan_results}. Berücksichtige Sektoren-Rotation und News-Sentiment."
            st.info(get_gemini_response(prompt))

    st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")