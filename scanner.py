import streamlit as st
import pandas as pd
import time
from datetime import datetime
from supabase import create_client, Client

# --- 1. SETTINGS & API (SUPABASE) ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

# Verbindung herstellen
@st.cache_resource
def get_supabase():
    url = st.secrets["DB_URL"]
    key = st.secrets["DB_TOKEN"]
    return create_client(url, key)

supabase = get_supabase()

# --- 2. LOGIN (Miros & Bianca) ---
def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login_form"):
            pw = st.text_input("Passwort für Miros & Bianca", type="password")
            if st.form_submit_button("Anmelden"):
                if pw == st.secrets["PASSWORD"]:
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("❌ Passwort falsch.")
        return False
    return True

if check_password():
    # --- 3. SIDEBAR ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        st.radio("Navigation", ["🔴 Live Radar", "⚪ Backtest", "🧠 AI Research"], label_visibility="collapsed")
        
        st.divider()
        st.subheader("Scanner & Strategien")
        strat_1 = st.selectbox("Strategie 1", ["Gap Momentum", "RSI Breakout"])
        markt_tiefe = st.slider("Markt-Tiefe", 0, 1000, 500)
        
        # DER HAKEN FÜR PRE/POST MARKET
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True)
        st.toggle("Telegram Alarme 📟", value=True)
        
        # SCAN BUTTON & IDLE STATUS
        st.divider()
        start_scan = st.button("🚀 SCANNER JETZT STARTEN", use_container_width=True, type="primary")
        
        if not start_scan:
            st.info("🟢 **Status: Idle** (Bereit)")

    # --- 4. HAUPTBEREICH ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_left, col_right = st.columns([1.8, 1])

    with col_left:
        st.subheader("🌐 Sektor Performance (Live)")
        # Dein TradingView Widget (SPY)
        st.components.v1.html("""
            <iframe src="https://s.tradingview.com/widgetembed/?symbol=CBOE%3ASPY&interval=5&theme=dark" width="100%" height="500" frameborder="0"></iframe>
        """, height=500)

    with col_right:
        st.subheader("📝 Signal Journal")
        journal_placeholder = st.empty()
        journal_placeholder.info("Warte auf Scan-Start...")

    # --- 5. SCANNER LOGIK & STATUS ---
    if start_scan:
        with st.status("🔍 Alpha Station scannt...", expanded=True) as status:
            st.write("Rufe Daten von Supabase ab...")
            
            try:
                # Abfrage (Haken-Logik wird hier für die Query genutzt)
                query = supabase.table("market_data").select("*").limit(markt_tiefe)
                if not include_prepost:
                    query = query.eq("session", "regular")
                
                res = query.execute()
                data = res.data
                
                st.write(f"{len(data)} Ticker analysiert...")
                time.sleep(1) # Kurze Pause für die Optik
                
                results = []
                for item in data:
                    results.append({
                        "Time": datetime.now().strftime("%H:%M"),
                        "Ticker": item.get("ticker", "N/A"),
                        "Price": f"{item.get('price', 0):.2f}",
                        "Gap%": f"{item.get('gap', 0):+.2f}%",
                        "Signal": "BUY" if item.get('gap', 0) > 2 else "WATCH"
                    })
                
                status.update(label="✅ Scan abgeschlossen!", state="complete", expanded=False)
                
                if results:
                    journal_placeholder.table(pd.DataFrame(results))
            except Exception as e:
                st.error(f"Fehler: {e}")

    # --- 6. FOOTER (Personalisiert) ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1: st.caption(f"📍 8500 Gerlikon, Im weberlis rebberg 42")
    with f2: st.caption(f"👨‍👩‍👧‍👦 Admin: Miros & Bianca")
    with f3: st.caption(f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")