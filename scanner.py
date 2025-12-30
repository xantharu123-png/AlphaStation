import streamlit as st
import pandas as pd
import yfinance as yf
import time
from datetime import datetime

# --- 1. GRUNDEINSTELLUNGEN ---
st.set_page_config(page_title="Alpha Station Pro", layout="wide", initial_sidebar_state="expanded")

# --- 2. LOGIN-LOGIK (Verwendet deine Secrets) ---
def check_password():
    """Prüft das Passwort gegen die Streamlit Secrets."""
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        # Hinweis auf die Familie wie im User-Kontext hinterlegt
        st.write("Willkommen zurück. Bitte Passwort für den Zugang eingeben.")
        
        with st.form("login_form"):
            pw = st.text_input("Passwort", type="password")
            submit = st.form_submit_button("Anmelden")
            if submit:
                if pw == st.secrets["PASSWORD"]:
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("❌ Passwort falsch.")
        return False
    return True

if check_password():
    # --- 3. SIDEBAR (Alle Einstellungen & Parameter) ---
    st.sidebar.header("⚙️ Scanner Setup")
    
    # Pre/Post Market Checkbox (Wichtig: wird an yfinance übergeben)
    include_prepost = st.sidebar.checkbox("Pre & Post Market einbeziehen", value=True, 
                                        help="Berücksichtigt Kurse außerhalb der regulären Öffnungszeiten.")
    
    st.sidebar.divider()
    
    # Scanner Parameter
    min_change = st.sidebar.slider("Min. Änderung für Filter (%)", 0.0, 15.0, 1.5)
    refresh_rate = st.sidebar.selectbox("Auto-Refresh Intervall", 
                                       options=[0, 1, 5, 10, 30], 
                                       format_func=lambda x: "Aus" if x == 0 else f"Alle {x} Minuten")
    
    # Ticker Liste
    st.sidebar.subheader("📈 Symbole")
    ticker_input = st.sidebar.text_area("Tickers (mit Komma trennen)", 
                                      "AAPL,TSLA,NVDA,AMD,MSFT,META,AMZN,GOOGL,MSTR,COIN,BTC-USD,ETH-USD")
    ticker_list = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]

    # Info-Bereich in der Sidebar (Personalisiert)
    st.sidebar.divider()
    st.sidebar.info(f"📍 **Standort:** Gerlikon\n📅 **Datum:** {datetime.now().strftime('%d.%m.%Y')}")

    # --- 4. HAUPTBEREICH & STATUS ---
    st.title("🚀 Alpha Station Terminal")
    
    col_btn, col_info = st.columns([1, 3])
    
    with col_btn:
        # Großer Scan-Button
        start_scan = st.button("🔍 SCAN JETZT STARTEN", use_container_width=True, type="primary")
    
    with col_info:
        # Status-Anzeige (Scan vs. Idle)
        if not start_scan:
            st.info("🟢 **Status: Idle** (Warte auf Befehl oder Auto-Refresh)")

    # --- 5. SCANNER ENGINE ---
    if start_scan or (refresh_rate > 0 and "last_scan" not in st.session_state):
        results = []
        
        # Visuelles Feedback während des Scans
        with st.status("🔍 Markt-Scan läuft...", expanded=True) as status:
            progress_bar = st.progress(0)
            
            for idx, symbol in enumerate(ticker_list):
                status.write(f"Rufe Daten ab für: **{symbol}**...")
                
                try:
                    ticker_obj = yf.Ticker(symbol)
                    # Abfrage mit Pre/Post Market Option
                    hist = ticker_obj.history(period="2d", interval="1m", prepost=include_prepost)
                    
                    if not hist.empty:
                        current_price = hist['Close'].iloc[-1]
                        # Vergleich mit dem letzten Schlusskurs (Vortag oder Pre-Market Start)
                        prev_close = hist['Close'].iloc[0] 
                        change_pct = ((current_price - prev_close) / prev_close) * 100
                        volume = hist['Volume'].iloc[-1]
                        
                        # Filter anwenden
                        if abs(change_pct) >= min_change:
                            results.append({
                                "Symbol": symbol,
                                "Preis ($)": f"{current_price:.2f}",
                                "Änderung (%)": f"{change_pct:+.2f}%",
                                "Volumen": f"{volume:,.0f}",
                                "Zeit": datetime.now().strftime("%H:%M:%S")
                            })
                    
                    # Fortschrittsbalken aktualisieren
                    progress_bar.progress((idx + 1) / len(ticker_list))
                    
                except Exception as e:
                    st.warning(f"Fehler bei {symbol}: {e}")
            
            st.session_state["last_scan"] = datetime.now()
            status.update(label=f"✅ Scan abgeschlossen um {datetime.now().strftime('%H:%M:%S')}", 
                         state="complete", expanded=False)

        # --- 6. ERGEBNIS-ANZEIGE ---
        if results:
            st.subheader(f"Gefundene Signale ({len(results)})")
            df = pd.DataFrame(results)
            
            # Styling der Tabelle (Grün für Plus, Rot für Minus)
            def color_coding(val):
                if isinstance(val, str) and '+' in val: return 'color: #00ff00; font-weight: bold'
                if isinstance(val, str) and '-' in val: return 'color: #ff4b4b; font-weight: bold'
                return ''

            st.table(df.style.applymap(color_coding, subset=['Änderung (%)']))
        else:
            st.warning("Keine Symbole gefunden, die das Filter-Kriterium erfüllen.")

    # --- 7. AUTO-REFRESH LOGIK ---
    if refresh_rate > 0:
        time.sleep(refresh_rate * 60)
        st.rerun()

    # --- FOOTER ---
    st.divider()
    st.caption(f"Alpha Station v1.5 | Angemeldet als Admin | Standort: 8500 Gerlikon")