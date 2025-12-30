import streamlit as st
import pandas as pd
import yfinance as yf
import time
from datetime import datetime

# --- 1. KONFIGURATION & LOGIN ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

def check_password():
    """Prüft das Passwort aus den Streamlit Secrets."""
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        st.write("Bitte gib das Passwort ein, um auf das Terminal zuzugreifen.")
        
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

# Programm startet nur, wenn Login erfolgreich
if check_password():
    
    # --- 2. SIDEBAR (Layout wie im Screenshot) ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("Navigation")
        nav = st.radio("Navigation", ["🔴 Live Radar", "⚪ Backtest", "🧠 AI Research"], label_visibility="collapsed")
        
        st.divider()
        st.subheader("Scanner & Strategien")
        
        strat_1 = st.selectbox("Strategie 1 (Momentum)", ["Gap Momentum", "RSI Breakout", "Volume Surge"])
        strat_2 = st.selectbox("Strategie 2 (Filter)", ["Keine", "Market Cap > 1B", "High Volatility"])
        
        markt_tiefe = st.slider("Markt-Tiefe", 10, 1000, 500)
        
        # --- DER GEWÜNSCHTE HAKEN ---
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True, 
                                      help="Aktivieren, um Kurse außerhalb der regulären US-Börsenzeiten zu sehen.")
        
        st.toggle("Telegram Alarme 📟", value=True)
        
        # --- DER SCAN-BUTTON & IDLE STATUS ---
        st.divider()
        start_scan = st.button("🚀 SCANNER JETZT STARTEN", use_container_width=True, type="primary")
        
        if not start_scan:
            st.info("🟢 **Status: Idle** (Bereit)")
        
        st.button("Journal & Datei löschen", use_container_width=True)

    # --- 3. HAUPTBEREICH (Layout: Chart links, Journal rechts) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    
    col_chart, col_journal = st.columns([2, 1])

    with col_chart:
        st.subheader("🌐 Sektor Performance (Live)")
        # Platzhalter für deinen TradingView Chart oder Performance-Map
        st.image("https://tradingview.com/static/images/free-widgets/mini-chart.png", caption="Live Chart Feed (Beispiel)")
        st.write("Hier wird dein Chart-Widget geladen...")

    with col_journal:
        st.subheader("📝 Signal Journal")
        # Container für die Tabelle
        journal_placeholder = st.empty()
        journal_placeholder.info("Warte auf Scan-Ergebnisse...")

    # --- 4. SCANNER LOGIK (MIT ECHTEM STATUS) ---
    if start_scan:
        # Hier erscheint die Status-Box im Hauptbereich
        with st.status("🔍 Alpha Station scannt gerade...", expanded=True) as status:
            st.write("Rufe Ticker-Daten ab...")
            
            # Beispiel-Liste (Hier kannst du deine volle Liste einfügen)
            tickers = ["AAPL", "TSLA", "NVDA", "AMD", "MSFT", "MSTR", "BTC-USD"]
            results = []
            
            for symbol in tickers:
                st.write(f"Analysiere: {symbol}...")
                try:
                    t = yf.Ticker(symbol)
                    # Hier wird der Haken 'include_prepost' angewendet!
                    data = t.history(period="2d", interval="1m", prepost=include_prepost)
                    
                    if not data.empty:
                        last_p = data['Close'].iloc[-1]
                        open_p = data['Open'].iloc[0]
                        gap = ((last_p - open_p) / open_p) * 100
                        
                        results.append({
                            "Time": datetime.now().strftime("%H:%M"),
                            "Ticker": symbol,
                            "Price": f"{last_p:.2f}",
                            "Gap%": f"{gap:+.2f}%",
                            "Signal": "BUY" if gap > 2 else "WATCH",
                            "Sentiment": "Bullish" if gap > 0 else "Bearish"
                        })
                    time.sleep(0.1)
                except Exception as e:
                    st.error(f"Fehler bei {symbol}: {e}")
            
            status.update(label="✅ Scan abgeschlossen!", state="complete", expanded=False)

        # Tabelle im Journal aktualisieren
        if results:
            df = pd.DataFrame(results)
            journal_placeholder.table(df)
            st.toast(f"Scan für {len(tickers)} Symbole beendet!")

    # --- 5. FOOTER (Deine persönlichen Daten/Infos) ---
    st.divider()
    f_col1, f_col2, f_col3 = st.columns(3)
    with f_col1:
        st.caption(f"📍 **Standort:** Landhaus, 8500 Gerlikon")
    with f_col2:
        st.caption(f"👨‍👩‍👧‍👦 **Alpha Station Admin:** Miros & Bianca")
    with f_col3:
        st.caption(f"🕒 **Letztes Update:** {datetime.now().strftime('%H:%M:%S')}")

# --- AUTO-REFRESH (Optional) ---
# Falls du möchtest, dass er alle 5 Min scannt, entkommentiere:
# time.sleep(300)
# st.rerun()