import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime

# --- 1. SETUP & LOGIN ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login_form"):
            # Korrektur: Miroslav statt Miros
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
    # Session State initialisieren, damit der Chart sich die Aktie merkt
    if "selected_symbol" not in st.session_state:
        st.session_state.selected_symbol = "SPY"
    if "scan_results" not in st.session_state:
        st.session_state.scan_results = []

    # --- 2. SIDEBAR (Strikte Strategien) ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("Scanner-Steuerung")
        # Deine gewünschten Dropdowns
        main_strat = st.selectbox("Hauptstrategie (Momentum)", 
                                 ["Volume Surge", "Gap Momentum", "RSI Breakout"])
        extra_strat = st.selectbox("Zusatzstrategie (Filter)", 
                                  ["Keine", "Penny Stocks (< $10)", "Market Cap > 1B"])
        
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True)
        st.toggle("Telegram Alarme 📟", value=True)
        
        # START BUTTON
        start_scan = st.button("🚀 MARKT-SCAN JETZT STARTEN", use_container_width=True, type="primary")
        
        if not start_scan:
            st.info("🟢 **Status: Idle** (Bereit für Miroslav)")

    # --- 3. HAUPTBEREICH (Layout) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    
    col_chart, col_journal = st.columns([1.8, 1])

    # --- 4. SCANNER LOGIK (FMP API) ---
    if start_scan:
        with st.status("🔍 Alpha Station durchsucht US-Börsen...", expanded=True) as status:
            # API Key Check
            api_key = st.secrets.get("API_KEY")
            if not api_key:
                st.error("API_KEY fehlt in Streamlit Secrets!")
                st.stop()
            
            # Strategie-Routing
            # Volume Surge -> Actives (findet SOPA automatisch)
            # Gap Momentum -> Gainers
            if main_strat == "Gap Momentum":
                url = f"https://financialmodelingprep.com/api/v3/stock_market/gainers?apikey={api_key}"
            else:
                url = f"https://financialmodelingprep.com/api/v3/stock_market/actives?apikey={api_key}"
            
            try:
                st.write(f"Kontaktiere FMP API für {main_strat}...")
                response = requests.get(url)
                
                if response.status_code == 200:
                    data = response.json()
                    results = []
                    
                    for stock in data:
                        symbol = stock.get("symbol")
                        change = stock.get("changesPercentage", 0)
                        price = stock.get("price", 0)
                        
                        # STRIKTE FILTERUNG
                        match = True
                        if extra_strat == "Penny Stocks (< $10)" and price >= 10: match = False
                        
                        if match:
                            results.append({
                                "Ticker": symbol,
                                "Price": f"${price:.2f}",
                                "Chg%": change,
                                "Time": datetime.now().strftime("%H:%M")
                            })
                    
                    # Sortierung & Speicherung
                    if results:
                        st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                        # Automatischer Chart-Update auf den Top-Treffer (z.B. SOPA)
                        st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                        status.update(label=f"✅ {len(results)} Treffer gefunden!", state="complete", expanded=False)
                    else:
                        st.session_state.scan_results = []
                        status.update(label="⚠️ Keine Treffer gefunden.", state="error")
                else:
                    st.error(f"API Fehler: {response.status_code}")
            except Exception as e:
                st.error(f"Verbindungsfehler: {e}")

    # --- 5. CHART ANZEIGE (DYNAMISCH) ---
    with col_chart:
        # Falls wir Ergebnisse haben, zeige ein Dropdown zum Wechseln der Charts
        if st.session_state.scan_results:
            ticker_list = [r['Ticker'] for r in st.session_state.scan_results]
            st.session_state.selected_symbol = st.selectbox("🎯 Wähle Treffer zum Anzeigen:", ticker_list)

        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        # Stabiler TradingView Chart (kein CBOE: Fehler mehr)
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f"""
            <iframe src="{chart_url}" width="100%" height="500" frameborder="0" allowtransparency="true" scrolling="no"></iframe>
        """, height=500)

    # --- 6. SIGNAL JOURNAL ---
    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            # Chg% formatieren für die Tabelle
            df['Chg%'] = df['Chg%'].apply(lambda x: f"{x:+.2f}%")
            st.table(df)
        else:
            st.info("Warte auf Scan... (Suche nach Volumenspikes)")

    # --- 7. FOOTER (Miroslav) ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1: st.caption("📍 8500 Gerlikon | Landhaus Terminal")
    with f2: st.caption(f"⚙️ **Admin:** Miroslav | Strategie: {main_strat}")
    with f3: st.caption(f"🕒 Stand: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")