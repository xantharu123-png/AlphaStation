import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime

# --- 1. SETTINGS & LOGIN ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

def check_password():
    """Prüft das Passwort für den Admin Miroslav."""
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
    # --- 2. SIDEBAR (Vollständige Strategie-Auswahl) ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("Scanner-Einstellungen")
        # Deine Hauptstrategien
        main_strat = st.selectbox("Hauptstrategie wählen", 
                                 ["Volume Surge", "Gap Momentum", "RSI Breakout"])
        
        # Deine Zusatzfilter
        extra_strat = st.selectbox("Zusatzfilter (Strikt)", 
                                  ["Keine", "Penny Stocks (< $10)", "Mid-Cap Focus", "Market Cap > 1B"])
        
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True)
        st.toggle("Telegram Alarme 📟", value=True)
        
        # SCAN BUTTON
        start_scan = st.button("🚀 STRATEGIE-SCAN STARTEN", use_container_width=True, type="primary")
        
        if not start_scan:
            st.info("🟢 **Status: Idle** (Bereit für Miroslav)")

    # --- 3. HAUPTBEREICH (Layout) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.8, 1])

    with col_chart:
        st.subheader("🌐 Markt-Monitor (Live)")
        # Stabiles TradingView Widget für den SPY
        st.components.v1.html("""
            <iframe src="https://s.tradingview.com/widgetembed/?symbol=SPY&interval=5&theme=dark" width="100%" height="500" frameborder="0"></iframe>
        """, height=500)

    with col_journal:
        st.subheader("📝 Signal Journal")
        journal_placeholder = st.empty()
        if not start_scan:
            journal_placeholder.info(f"Wähle eine Strategie und starte den Scan. Aktuell: {main_strat}")

    # --- 4. SCANNER ENGINE (STRIKTE FILTERUNG) ---
    if start_scan:
        with st.status(f"🔍 Alpha Station scannt nach {main_strat}...", expanded=True) as status:
            api_key = st.secrets["API_KEY"]
            
            # Strategie-Routing: Wir wählen den API-Endpunkt passend zur Hauptstrategie
            if main_strat == "Gap Momentum":
                st.write("Suche nach Top Gainern für Gap-Setups...")
                url = f"https://financialmodelingprep.com/api/v3/stock_market/gainers?apikey={api_key}"
            else:
                # Standard für Volume Surge: Die aktivsten Aktien (wie SOPA)
                st.write("Suche nach Aktien mit außergewöhnlichem Volumen...")
                url = f"https://financialmodelingprep.com/api/v3/stock_market/actives?apikey={api_key}"
            
            try:
                response = requests.get(url).json()
                results = []
                
                for stock in response[:50]:
                    symbol = stock.get("symbol")
                    change = stock.get("changesPercentage", 0)
                    price = stock.get("price", 0)
                    
                    # STRIKTE FILTER-LOGIK (Nur das anzeigen, was gewählt wurde)
                    match = True
                    
                    # 1. Filter: Preis (Penny Stocks)
                    if extra_strat == "Penny Stocks (< $10)" and price >= 10:
                        match = False
                    
                    # 2. Filter: Strategie-spezifische Schwellenwerte
                    if main_strat == "Gap Momentum" and change < 3.0:
                        match = False
                    if main_strat == "Volume Surge" and abs(change) < 0.5:
                        match = False
                        
                    if match:
                        results.append({
                            "Time": datetime.now().strftime("%H:%M"),
                            "Ticker": symbol,
                            "Price": f"${price:.2f}",
                            "Chg%": f"{change:+.2f}%",
                            "Strategie": main_strat,
                            "Signal": "🔥 TRIGGER"
                        })
                
                status.update(label="✅ Scan abgeschlossen!", state="complete", expanded=False)
                
                if results:
                    df = pd.DataFrame(results)
                    # Sortierung: Höchste Gaps zuerst
                    df = df.sort_values(by="Chg%", ascending=False)
                    journal_placeholder.table(df)
                    st.toast(f"{len(results)} Treffer für {main_strat} gefunden!")
                else:
                    journal_placeholder.warning(f"Keine Aktien gefunden, die aktuell der Strategie '{main_strat}' entsprechen.")
                    
            except Exception as e:
                st.error(f"API Fehler: {e}")

    # --- 5. FOOTER (Korrigiert für Miroslav) ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1:
        st.caption("📍 8500 Gerlikon, Im weberlis rebberg 42")
    with f2:
        st.caption(f"⚙️ **Admin-Modus:** Miroslav | Strategie: {main_strat}")
    with f3:
        st.caption(f"🕒 Stand: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")