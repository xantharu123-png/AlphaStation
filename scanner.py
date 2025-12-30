import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime

# --- 1. SETTINGS & LOGIN ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

def check_password():
    """Prüft das Passwort für den Admin-Zugang."""
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        st.write(f"Willkommen im Terminal. Heute ist der {datetime.now().strftime('%d.%m.%Y')}.")
        with st.form("login_form"):
            pw = st.text_input("Passwort", type="password")
            if st.form_submit_button("Anmelden"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("❌ Passwort falsch.")
        return False
    return True

if check_password():
    # --- 2. SIDEBAR ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("Navigation")
        st.radio("Nav", ["🔴 Live Radar", "⚪ Backtest", "🧠 AI Research"], label_visibility="collapsed")
        
        st.divider()
        st.subheader("Scanner & Strategien")
        
        strat = st.selectbox("Strategie", ["Gap Momentum", "High Volatility", "RSI Breakout"])
        
        # Deine Watchlist
        ticker_input = st.text_area("Watchlist Ticker (Komma-getrennt)", "AAPL,TSLA,NVDA,AMD,MSFT,MSTR,COIN,MARA")
        ticker_list = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
        
        # --- DER PRE/POST MARKET HAKEN ---
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True)
        
        st.toggle("Telegram Alarme 📟", value=True)
        
        # --- SCAN BUTTON & STATUS ANZEIGE ---
        st.divider()
        start_scan = st.button("🚀 SCANNER JETZT STARTEN", use_container_width=True, type="primary")
        
        # Visueller Status: IDLE
        if not start_scan:
            st.info("🟢 **Status: Idle** (Bereit)")
        
        st.button("Journal & Datei löschen", use_container_width=True)

    # --- 3. HAUPTBEREICH (Layout wie gewünscht) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    
    col_chart, col_journal = st.columns([1.8, 1])

    with col_chart:
        st.subheader("🌐 Sektor Performance (Live)")
        # TradingView Widget
        st.components.v1.html("""
            <iframe src="https://s.tradingview.com/widgetembed/?symbol=CBOE%3ASPY&interval=5&theme=dark" width="100%" height="500" frameborder="0"></iframe>
        """, height=500)

    with col_journal:
        st.subheader("📝 Signal Journal")
        journal_placeholder = st.empty()
        journal_placeholder.info("Warte auf Scan-Befehl...")

    # --- 4. SCANNER ENGINE (FMP API DIREKT) ---
    if start_scan:
        # Visueller Status: SCANNING
        with st.status("🔍 Alpha Station scannt gerade...", expanded=True) as status:
            st.write("Verbindung zur FMP API wird hergestellt...")
            
            # API Key aus deinen Secrets
            api_key = st.secrets["API_KEY"]
            results = []
            
            for symbol in ticker_list:
                status.write(f"Analysiere: **{symbol}**...")
                
                # Direkte API-Abfrage an FMP (keine Datenbank/Tabelle!)
                url = f"https://financialmodelingprep.com/api/v3/quote/{symbol}?apikey={api_key}"
                
                try:
                    response = requests.get(url).json()
                    if response and isinstance(response, list):
                        d = response[0]
                        price = d.get("price", 0)
                        change = d.get("changesPercentage", 0)
                        
                        results.append({
                            "Time": datetime.now().strftime("%H:%M"),
                            "Ticker": symbol,
                            "Price": f"{price:.2f}",
                            "Gap%": f"{change:+.2f}%",
                            "Signal": "BUY" if change > 1.5 else "WATCH",
                            "Info": "🌙 Pre/Post" if include_prepost else "☀️ Reg"
                        })
                    time.sleep(0.1) # Schont dein 250er-Limit
                except Exception as e:
                    st.error(f"Fehler bei {symbol}: {e}")
            
            status.update(label="✅ Scan abgeschlossen!", state="complete", expanded=False)

        # Tabelle rechts füllen
        if results:
            df = pd.DataFrame(results)
            journal_placeholder.table(df)
        else:
            journal_placeholder.warning("Keine Daten empfangen.")

    # --- 5. FOOTER ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1:
        st.caption(f"📍 8500 Gerlikon, Im weberlis rebberg 42")
    with f2:
        st.caption(f"⚙️ **Admin-Modus aktiv**")
    with f3:
        st.caption(f"🕒 Stand: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")