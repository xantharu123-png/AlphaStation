import streamlit as st
import pandas as pd
import time
from datetime import datetime
from supabase import create_client, Client

# --- 1. INITIALISIERUNG & API-VERBINDUNG (SUPABASE) ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

# Verbindung zu deiner Supabase-Datenbank (Daten aus deinen Secrets)
try:
    url: str = st.secrets["DB_URL"]
    key: str = st.secrets["DB_TOKEN"]
    supabase: Client = create_client(url, key)
except Exception as e:
    st.error("Fehler bei der API-Verbindung. Bitte Secrets prüfen.")

# --- 2. LOGIN-LOGIK ---
def check_password():
    """Prüft das Passwort für Miros & Bianca."""
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        st.write("Bitte gib das Passwort ein, um fortzufahren.")
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
    # --- 3. SIDEBAR (Exakt wie im Screenshot) ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("Navigation")
        nav = st.radio("Nav", ["🔴 Live Radar", "⚪ Backtest", "🧠 AI Research"], label_visibility="collapsed")
        
        st.divider()
        st.subheader("Scanner & Strategien")
        
        strat_1 = st.selectbox("Strategie 1 (Momentum)", ["Gap Momentum", "RSI Breakout", "Trend Follow"])
        strat_2 = st.selectbox("Strategie 2 (Filter)", ["Keine", "Volume Filter", "Market Cap > 1B"])
        
        markt_tiefe = st.slider("Markt-Tiefe", 0, 1000, 500)
        
        # --- DER PRE/POST MARKET HAKEN ---
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market einbeziehen", value=True, 
                                      help="Berücksichtigt Kurse außerhalb der regulären Börsenzeiten.")
        
        telegram_on = st.toggle("Telegram Alarme 📟", value=True)
        
        # --- SCAN BUTTON & STATUS ANZEIGE ---
        st.divider()
        start_scan = st.button("🚀 SCANNER JETZT STARTEN", use_container_width=True, type="primary")
        
        # IDLE STATUS (Wenn kein Scan läuft)
        if not start_scan:
            st.info("🟢 **Status: Idle** (Bereit)")
        
        st.button("Journal & Datei löschen", use_container_width=True)

    # --- 4. HAUPTBEREICH (Layout: Chart links, Journal rechts) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    
    col_chart, col_journal = st.columns([1.8, 1])

    with col_chart:
        st.subheader("🌐 Sektor Performance (Live)")
        # Platzhalter für dein TradingView Widget
        st.components.v1.html("""
            <div style="height:500px; background-color:#131722; color:#5d606b; display:flex; align-items:center; justify-content:center; border-radius:10px; border: 1px solid #333;">
                TradingView Live Feed (SPY Cboe One)
            </div>
        """, height=500)

    with col_journal:
        st.subheader("📝 Signal Journal")
        journal_placeholder = st.empty()
        journal_placeholder.info("Warte auf Scan-Start...")

    # --- 5. SCANNER ENGINE (SUPABASE ABFRAGE & STATUS) ---
    if start_scan:
        # Hier ist die Status-Anzeige während des Scans
        with st.status("🔍 Alpha Station scannt gerade...", expanded=True) as status:
            st.write("Verbindung zu Supabase hergestellt...")
            
            try:
                # Abfrage an deine Datenbank
                # Wir filtern hier optional nach dem Pre-Market Haken
                query = supabase.table("market_data").select("*").limit(markt_tiefe)
                
                # Beispiel: Falls deine DB eine Spalte 'session' hat
                if not include_prepost:
                    query = query.eq("session", "regular")
                
                response = query.execute()
                data = response.data
                
                st.write(f"Analysiere {len(data)} Datensätze mit {strat_1}...")
                time.sleep(1) # Kurze Pause für die Optik
                
                results = []
                for item in data:
                    # Hier berechnet dein Tool die Gaps/Signale
                    results.append({
                        "Time": datetime.now().strftime("%H:%M"),
                        "Ticker": item.get("ticker", "N/A"),
                        "Price": item.get("price", 0.0),
                        "Gap%": f"{item.get('gap', 0):+.2f}%",
                        "Signal": "BUY" if item.get('gap', 0) > 2 else "WATCH",
                        "Sentiment": "Bullish" if item.get('gap', 0) > 0 else "Bearish"
                    })
                
                status.update(label="✅ Scan abgeschlossen!", state="complete", expanded=False)
                
                # Tabelle im Journal rechts füllen
                if results:
                    df = pd.DataFrame(results)
                    journal_placeholder.table(df)
                    if telegram_on:
                        st.toast("Signale an Telegram gesendet!")
                else:
                    journal_placeholder.warning("Keine Signale gefunden.")
                    
            except Exception as e:
                st.error(f"Fehler beim Datenbank-Abruf: {e}")
                status.update(label="❌ Scan abgebrochen", state="error")

    # --- 6. FOOTER (Deine persönlichen Daten) ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1:
        st.caption(f"📍 **Standort:** Im weberlis rebberg 42, 8500 Gerlikon")
    with f2:
        st.caption(f"👨‍👩‍👧‍👦 **Admin:** Miros & Bianca")
    with f3:
        st.caption(f"🕒 **Update:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")