import streamlit as st
import pandas as pd
import time
from datetime import datetime
from supabase import create_client, Client

# --- 1. SETTINGS & LOGIN ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

# Login-Logik für dich und Bianca (Gerlikon)
def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login_form"):
            pw = st.text_input("Passwort für Miros & Bianca", type="password")
            if st.form_submit_button("Anmelden"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("❌ Passwort falsch.")
        return False
    return True

if check_password():
    # --- 2. SIDEBAR (Exakt wie in deinem Screenshot + Ergänzungen) ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("Navigation")
        st.radio("Nav", ["🔴 Live Radar", "⚪ Backtest", "🧠 AI Research"], label_visibility="collapsed")
        
        st.divider()
        st.subheader("Scanner & Strategien")
        strat_1 = st.selectbox("Strategie 1 (Momentum)", ["Gap Momentum", "RSI Breakout"])
        strat_2 = st.selectbox("Strategie 2 (Filter)", ["Keine", "Volume Filter"])
        markt_tiefe = st.slider("Markt-Tiefe", 0, 1000, 500)
        
        # --- NEU: PRE/POST MARKET HAKEN ---
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True)
        
        st.toggle("Telegram Alarme 📟", value=True)
        
        # --- SCAN BUTTON & STATUS ---
        st.divider()
        start_scan = st.button("🚀 SCANNER JETZT STARTEN", use_container_width=True, type="primary")
        
        # NEU: STATUS ANZEIGE (IDLE)
        if not start_scan:
            st.info("🟢 **Status: Idle** (Bereit)")
        
        st.button("Journal & Datei löschen", use_container_width=True)

    # --- 3. HAUPTBEREICH (Zwei Spalten wie im Bild) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    
    col_left, col_right = st.columns([1.8, 1])

    with col_left:
        st.subheader("🌐 Sektor Performance (Live)")
        # TradingView Widget (wie in deinem Screenshot)
        st.components.v1.html("""
            <div style="height:500px;"><iframe src="https://s.tradingview.com/widgetembed/?frameElementId=tradingview_1&symbol=CBOE%3ASPY&interval=5&hidesidetoolbar=1&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=dark&style=1&timezone=Europe%2FZurich" width="100%" height="500" frameborder="0" allowtransparency="true" scrolling="no"></iframe></div>
        """, height=500)

    with col_right:
        st.subheader("📝 Signal Journal")
        journal_placeholder = st.empty()
        journal_placeholder.info("Warte auf Scan-Befehl...")

    # --- 4. SCANNER LOGIK (SUPABASE API) ---
    if start_scan:
        # NEU: STATUS WÄHREND DES SCANS
        with st.status("🔍 Alpha Station scannt gerade...", expanded=True) as status:
            st.write("Verbindung zu Supabase wird aufgebaut...")
            
            try:
                # Verbindung zur API (Supabase)
                url = st.secrets["DB_URL"]
                key = st.secrets["DB_TOKEN"]
                supabase: Client = create_client(url, key)
                
                # Daten abrufen (Haken für Pre/Post wird hier berücksichtigt)
                st.write(f"Rufe {markt_tiefe} Datensätze ab...")
                query = supabase.table("market_data").select("*").limit(markt_tiefe)
                if not include_prepost:
                    query = query.eq("session", "regular")
                
                response = query.execute()
                data = response.data
                
                results = []
                for item in data:
                    results.append({
                        "Time": datetime.now().strftime("%H:%M"),
                        "Ticker": item.get("ticker"),
                        "Price": f"{item.get('price'):.2f}",
                        "Gap%": f"{item.get('gap'):+.2f}%",
                        "Signal": "BUY" if item.get('gap') > 2 else "WATCH",
                        "Sentiment": "Bullish" if item.get('gap') > 0 else "Bearish",
                        "Info": "🌙 Pre" if include_prepost else "☀️ Reg"
                    })
                
                status.update(label="✅ Scan abgeschlossen!", state="complete", expanded=False)
                
                if results:
                    journal_placeholder.table(pd.DataFrame(results))
                else:
                    journal_placeholder.warning("Keine Signale gefunden.")
                    
            except Exception as e:
                st.error(f"Fehler beim Scan: {e}")

    # --- 5. FOOTER (Deine persönlichen Daten) ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1: st.caption(f"📍 **Standort:** Im weberlis rebberg 42, 8500 Gerlikon")
    with f2: st.caption(f"👨‍👩‍👧‍👦 **Admin:** Miros & Bianca")
    with f3: st.caption(f"🕒 **Zeit:** {datetime.now().strftime('%H:%M:%S')}")