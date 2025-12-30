import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime

# --- 1. SETTINGS & LOGIN ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login_form"):
            pw = st.text_input("Admin-Passwort eingeben", type="password")
            if st.form_submit_button("Anmelden"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("❌ Passwort falsch.")
        return False
    return True

if check_password():
    # Session State für Scan-Ergebnisse initialisieren
    if "scan_results" not in st.session_state:
        st.session_state.scan_results = []
    if "selected_symbol" not in st.session_state:
        st.session_state.selected_symbol = "SPY"

    # --- 2. SIDEBAR ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("Strategie-Filter")
        main_strat = st.selectbox("Nur nach dieser Strategie suchen:", 
                                 ["Volume Surge", "Gap Momentum", "RSI Breakout"])
        
        extra_strat = st.selectbox("Zusatzfilter (Strikt)", 
                                  ["Keine", "Penny Stocks (< $10)", "Mid-Cap Focus"])
        
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True)
        st.toggle("Telegram Alarme 📟", value=True)
        
        start_scan = st.button("🚀 STRATEGIE-SCAN STARTEN", use_container_width=True, type="primary")
        
        if not start_scan:
            st.info("🟢 **Status: Idle** (Bereit für Miroslav)")

    # --- 3. HAUPTBEREICH ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.8, 1])

    # --- 4. SCANNER LOGIK (VOR der Chart-Anzeige, damit Daten da sind) ---
    if start_scan:
        with st.status(f"🔍 Alpha Station sucht {main_strat}...", expanded=True) as status:
            api_key = st.secrets.get("API_KEY")
            if not api_key:
                st.error("API_KEY fehlt!")
                st.stop()
            
            # API Routing
            if main_strat == "Gap Momentum":
                url = f"https://financialmodelingprep.com/api/v3/stock_market/gainers?apikey={api_key}"
            else:
                url = f"https://financialmodelingprep.com/api/v3/stock_market/actives?apikey={api_key}"
            
            try:
                response = requests.get(url)
                if response.status_code == 200:
                    data = response.json()
                    temp_results = []
                    
                    for stock in data:
                        symbol = stock.get("symbol")
                        change = stock.get("changesPercentage", 0)
                        price = stock.get("price", 0)
                        
                        # Striktes Filtering
                        match = True
                        if extra_strat == "Penny Stocks (< $10)" and price >= 10: match = False
                        if main_strat == "Gap Momentum" and change < 3.0: match = False
                        
                        if match:
                            temp_results.append({
                                "Ticker": symbol,
                                "Price": price,
                                "Chg%": change,
                                "Time": datetime.now().strftime("%H:%M")
                            })
                    
                    # Ergebnisse speichern und Top-Symbol auswählen
                    st.session_state.scan_results = temp_results
                    if temp_results:
                        # Sortieren nach Chg%
                        st.session_state.scan_results = sorted(temp_results, key=lambda x: x['Chg%'], reverse=True)
                        st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                    
                    status.update(label="✅ Scan abgeschlossen!", state="complete", expanded=False)
                else:
                    st.error("API Fehler!")
            except Exception as e:
                st.error(f"Fehler: {e}")

    # --- 5. CHART ANZEIGE (Dynamisch) ---
    with col_chart:
        # Wenn wir Treffer haben, zeige einen Selector über dem Chart
        if st.session_state.scan_results:
            ticker_options = [res['Ticker'] for res in st.session_state.scan_results]
            selected = st.selectbox("🎯 Treffer-Visualisierung wählen:", ticker_options, index=0)
            st.session_state.selected_symbol = selected

        st.subheader(f"📊 Chart: {st.session_state.selected_symbol}")
        
        # Das dynamische TradingView Widget
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f"""
            <iframe src="{chart_url}" width="100%" height="550" frameborder="0" allowtransparency="true" scrolling="no"></iframe>
        """, height=550)

    # --- 6. SIGNAL JOURNAL (Rechte Spalte) ---
    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            st.table(df[["Ticker", "Price", "Chg%", "Time"]])
        else:
            st.info("Warte auf Scan-Befehl...")

    # --- 7. FOOTER ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1: st.caption("📍 8500 Gerlikon, Im weberlis rebberg 42")
    with f2: st.caption(f"⚙️ **Admin:** Miroslav | Fokus: {st.session_state.selected_symbol}")
    with f3: st.caption(f"🕒 Stand: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")