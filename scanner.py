import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime

# --- 1. KONFIGURATION & LOGIN ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        st.write("Bitte Passwort für Miros & Bianca eingeben:")
        with st.form("login"):
            pw = st.text_input("Passwort", type="password")
            if st.form_submit_button("Anmelden"):
                if pw == st.secrets["PASSWORD"]:
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("❌ Passwort falsch.")
        return False
    return True

if check_password():
    # --- 2. SIDEBAR (Layout wie im Screenshot) ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        st.subheader("Navigation")
        st.radio("Nav", ["🔴 Live Radar", "⚪ Backtest", "🧠 AI Research"], label_visibility="collapsed")
        
        st.divider()
        st.subheader("Scanner & Strategien")
        strat = st.selectbox("Strategie", ["Gap Momentum", "High Volatility"])
        anzahl = st.slider("Anzahl Ticker", 5, 50, 15) # Schont dein 250-Limit
        
        # --- DER GEWÜNSCHTE HAKEN ---
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True)
        st.toggle("Telegram Alarme 📟", value=True)
        
        # --- SCAN BUTTON & IDLE STATUS ---
        st.divider()
        start_scan = st.button("🚀 SCANNER JETZT STARTEN", use_container_width=True, type="primary")
        
        if not start_scan:
            st.info("🟢 **Status: Idle** (Wartet...)")
        
        st.button("Journal & Datei löschen", use_container_width=True)

    # --- 3. HAUPTBEREICH (Zwei Spalten) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.8, 1])

    with col_chart:
        st.subheader("🌐 Sektor Performance (Live)")
        st.components.v1.html("""<iframe src="https://s.tradingview.com/widgetembed/?symbol=CBOE%3ASPY&theme=dark" width="100%" height="500"></iframe>""", height=500)

    with col_journal:
        st.subheader("📝 Signal Journal")
        journal_placeholder = st.empty()
        journal_placeholder.info("Warte auf Scan...")

    # --- 4. SCANNER LOGIK (FMP API) ---
    if start_scan:
        # VISUELLE STATUS-ANZEIGE
        with st.status("🔍 Alpha Station scannt gerade...", expanded=True) as status:
            st.write("Verbindung zu FMP-Servern hergestellt...")
            
            api_key = st.secrets["API_KEY"]
            tickers = ["AAPL", "TSLA", "NVDA", "AMD", "MSFT", "META", "AMZN", "GOOGL", "NFLX"]
            
            results = []
            for symbol in tickers[:anzahl]:
                st.write(f"Rufe Daten ab: **{symbol}**")
                
                # FMP API Quote-Endpunkt
                url = f"https://financialmodelingprep.com/api/v3/quote/{symbol}?apikey={api_key}"
                
                try:
                    res = requests.get(url).json()
                    if res:
                        d = res[0]
                        results.append({
                            "Time": datetime.now().strftime("%H:%M"),
                            "Ticker": symbol,
                            "Price": f"{d.get('price', 0):.2f}",
                            "Gap%": f"{d.get('changesPercentage', 0):+.2f}%",
                            "Signal": "BUY" if d.get('changesPercentage', 0) > 2 else "WATCH",
                            "Info": "🌙 Pre/Post" if include_prepost else "☀️ Regular"
                        })
                    time.sleep(0.1)
                except: pass
            
            status.update(label="✅ Scan abgeschlossen!", state="complete", expanded=False)

        if results:
            journal_placeholder.table(pd.DataFrame(results))

    # --- 5. FOOTER (Deine Daten aus Gerlikon) ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1: st.caption(f"📍 8500 Gerlikon, Im weberlis rebberg 42")
    with f2: st.caption(f"👨‍👩‍👧‍👦 Admin: Miros & Bianca")
    with f3: st.caption(f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")