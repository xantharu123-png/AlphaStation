import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime

# --- 1. SETUP ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login_form"):
            pw = st.text_input("Admin-Passwort Miroslav", type="password")
            if st.form_submit_button("Anmelden"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("❌ Passwort falsch.")
        return False
    return True

if check_password():
    if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "SPY"
    if "scan_results" not in st.session_state: st.session_state.scan_results = []

    # --- 2. SIDEBAR ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        st.subheader("Strategie-Auswahl")
        main_strat = st.selectbox("Hauptstrategie (Momentum)", ["Volume Surge", "Gap Momentum"])
        extra_strat = st.selectbox("Zusatzfilter", ["Keine", "Penny Stocks (< $10)"])
        
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market", value=True)
        
        start_scan = st.button("🚀 MARKT-SCAN STARTEN", use_container_width=True, type="primary")
        if not start_scan: st.info("🟢 **Status: Idle** (Bereit)")

    # --- 3. HAUPTBEREICH ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.8, 1])

    # --- 4. SCANNER LOGIK (FMP API) ---
    if start_scan:
        with st.status("🔍 Alpha Station durchsucht US-Börsen...", expanded=True) as status:
            api_key = st.secrets.get("API_KEY")
            
            # Endpunkt wählen
            # 'actives' ist perfekt, um Volumenspikes wie SOPA automatisch zu finden
            url = f"https://financialmodelingprep.com/api/v3/stock_market/actives?apikey={api_key}"
            if main_strat == "Gap Momentum":
                url = f"https://financialmodelingprep.com/api/v3/stock_market/gainers?apikey={api_key}"
            
            try:
                res = requests.get(url)
                if res.status_code == 403:
                    st.error("❌ API Fehler 403: Dein API-Key ist ungültig oder nicht für diesen Endpunkt freigeschaltet. Bitte prüfe deine Streamlit Secrets!")
                    st.stop()
                
                data = res.json()
                results = []
                for stock in data[:30]:
                    sym = stock.get("symbol")
                    chg = stock.get("changesPercentage", 0)
                    prc = stock.get("price", 0)
                    
                    # Striktes Filtern
                    match = True
                    if extra_strat == "Penny Stocks (< $10)" and prc >= 10: match = False
                    
                    if match:
                        results.append({"Ticker": sym, "Price": f"${prc:.2f}", "Chg%": chg, "Time": datetime.now().strftime("%H:%M")})
                
                if results:
                    st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                    st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                    status.update(label=f"✅ {len(results)} Treffer gefunden!", state="complete", expanded=False)
                else:
                    st.warning("Keine Treffer für diese Strategie.")
            except Exception as e:
                st.error(f"Fehler: {e}")

    # --- 5. DYNAMISCHER CHART ---
    with col_chart:
        if st.session_state.scan_results:
            ticker_list = [r['Ticker'] for r in st.session_state.scan_results]
            st.session_state.selected_symbol = st.selectbox("🎯 Chart auswählen:", ticker_list)

        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        # Stabiler TradingView Chart ohne CBOE-Fehler
        chart_code = f"""
            <iframe src="https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark" 
            width="100%" height="500" frameborder="0" allowtransparency="true" scrolling="no"></iframe>
        """
        st.components.v1.html(chart_code, height=500)

    # --- 6. JOURNAL ---
    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            df['Chg%'] = df['Chg%'].apply(lambda x: f"{x:+.2f}%")
            st.table(df)
        else:
            st.info("Warte auf Scan...")

    # --- 7. FOOTER ---
    st.divider()
    st.caption(f"📍 8500 Gerlikon | Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")