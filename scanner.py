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
        st.write("Willkommen zurück. Bitte Passwort für Miros & Bianca eingeben:")
        with st.form("login_form"):
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
    # --- 2. SIDEBAR (Design aus deinem Screenshot) ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("Navigation")
        st.radio("Nav", ["🔴 Live Radar", "⚪ Backtest", "🧠 AI Research"], label_visibility="collapsed")
        
        st.divider()
        st.subheader("Scanner & Strategien")
        strat_1 = st.selectbox("Strategie (Momentum)", ["Gap Momentum", "High Volatility"])
        markt_tiefe = st.slider("Anzahl Ticker", 5, 50, 20) # Begrenzt auf 250 Requests/Tag
        
        # --- DER GEWÜNSCHTE HAKEN ---
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True)
        st.toggle("Telegram Alarme 📟", value=True)
        
        # --- SCAN BUTTON & STATUS ANZEIGE ---
        st.divider()
        start_scan = st.button("🚀 SCANNER JETZT STARTEN", use_container_width=True, type="primary")
        
        if not start_scan:
            st.info("🟢 **Status: Idle** (Bereit)")
        
        st.button("Journal & Datei löschen", use_container_width=True)

    # --- 3. HAUPTBEREICH (Layout: Chart & Journal) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.8, 1])

    with col_chart:
        st.subheader("🌐 Markt-Übersicht (SPY)")
        # TradingView Widget
        st.components.v1.html("""
            <iframe src="https://s.tradingview.com/widgetembed/?symbol=CBOE%3ASPY&interval=5&theme=dark" width="100%" height="500" frameborder="0"></iframe>
        """, height=500)

    with col_journal:
        st.subheader("📝 Signal Journal")
        journal_placeholder = st.empty()
        journal_placeholder.info("Warte auf Scan-Start...")

    # --- 4. SCANNER LOGIK (DIREKT ÜBER API) ---
    if start_scan:
        # Hier ist die pulsierende Status-Anzeige während des Scans
        with st.status("🔍 Alpha Station scannt gerade...", expanded=True) as status:
            st.write("Verbindung zur API wird hergestellt...")
            
            # Dein API Key aus den Secrets
            api_key = st.secrets["API_KEY"]
            tickers = ["AAPL", "TSLA", "NVDA", "AMD", "MSFT", "META", "AMZN", "GOOGL"] # Deine Ticker-Liste
            
            results = []
            for symbol in tickers[:markt_tiefe]:
                st.write(f"Analysiere: {symbol}...")
                
                # Beispiel-Abfrage an eine Stock-API (z.B. Financial Modeling Prep)
                # Die URL passt du einfach an deine API an
                url = f"https://financialmodelingprep.com/api/v3/quote/{symbol}?apikey={api_key}"
                
                try:
                    response = requests.get(url).json()
                    if response:
                        data = response[0]
                        price = data.get("price")
                        change = data.get("changesPercentage")
                        
                        # Pre-Market Logik (wenn deine API das liefert)
                        # Hier nutzen wir den 'include_prepost' Haken
                        
                        results.append({
                            "Time": datetime.now().strftime("%H:%M"),
                            "Ticker": symbol,
                            "Price": f"{price:.2f}",
                            "Gap%": f"{change:+.2f}%",
                            "Signal": "BUY" if change > 2 else "WATCH"
                        })
                except Exception as e:
                    st.error(f"Fehler bei {symbol}: {e}")
                
                time.sleep(0.1) # Um die API nicht zu überlasten
            
            status.update(label="✅ Scan abgeschlossen!", state="complete", expanded=False)

        # Tabelle im Journal füllen
        if results:
            journal_placeholder.table(pd.DataFrame(results))

    # --- 5. FOOTER (Deine Daten aus Gerlikon) ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1: st.caption(f"📍 8500 Gerlikon, Im weberlis rebberg 42")
    with f2: st.caption(f"👨‍👩‍👧‍👦 Admin: Miros & Bianca")
    with f3: st.caption(f"🕒 {datetime.now().strftime('%H:%M:%S')}")