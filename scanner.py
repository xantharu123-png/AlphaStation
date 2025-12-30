import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

# --- 1. INITIALISIERUNG ---
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "SPY"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

# --- 2. HELPER FUNKTIONEN (KI & LOGIN) ---
def get_gemini_response(prompt):
    try:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
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

# --- 3. HAUPTPROGRAMM ---
if check_password():
    st.set_page_config(page_title="Alpha V33 Master", layout="wide")

    with st.sidebar:
        st.title("💎 Alpha V33 Master")
        
        # Strategie Auswahl (Beispielhaft gekürzt für Fokus auf Navigation)
        strat_list = ["Volume Surge", "Gap Momentum", "Penny Stock Breakout", "Multi-Day Runner", "Dead Cat Bounce"]
        main_strat = st.selectbox("Strategie", strat_list)
        
        if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
            with st.status("Scanne Markt...") as status:
                poly_key = st.secrets["POLYGON_KEY"]
                url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
                try:
                    resp = requests.get(url).json()
                    res = []
                    # Vereinfachte Beispiel-Logik für den Scan
                    for t in resp.get("tickers", [])[:100]: # Nur erste 100 für Speed
                        res.append({
                            "Ticker": t.get("ticker"), 
                            "Price": t.get("min", {}).get("c", 0), 
                            "Chg%": t.get("todaysChangePerc", 0),
                            "Vol": t.get("day", {}).get("v", 0)
                        })
                    st.session_state.scan_results = sorted(res, key=lambda x: x['Chg%'], reverse=True)
                    if st.session_state.scan_results:
                        st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                    status.update(label="Scan fertig!", state="complete")
                except: st.error("API Fehler")

    # --- HAUPTBEREICH (Navigation & Chart) ---
    c_chart, c_journal = st.columns([1.6, 1])
    
    with c_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            
            # --- DER NAVIGATION-KEY ---
            # Benutze Pfeiltasten + Enter zur Auswahl
            selection = st.dataframe(
                df, 
                on_select="rerun", 
                selection_mode="single-row", 
                hide_index=True, 
                use_container_width=True
            )
            
            # Wenn eine Zeile gewählt wird (per Klick oder Pfeiltaste+Enter)
            if selection.selection and selection.selection.rows:
                selected_index = selection.selection.rows[0]
                new_symbol = df.iloc[selected_index]["Ticker"]
                if new_symbol != st.session_state.selected_symbol:
                    st.session_state.selected_symbol = new_symbol
                    st.rerun() # Sofortiger Refresh für den Chart
        else:
            st.info("Bitte Scan starten.")

    with c_chart:
        st.subheader(f"📊 Chart: {st.session_state.selected_symbol}")
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="550" frameborder="0"></iframe>', height=550)

    # --- KI ANALYSE ---
    st.divider()
    if st.button(f"🤖 KI ANALYSE: {st.session_state.selected_symbol}"):
        with st.spinner("Analysiere..."):
            st.info(get_gemini_response(f"Kurzanalyse für {st.session_state.selected_symbol}."))

    st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")