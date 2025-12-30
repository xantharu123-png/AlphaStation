import streamlit as st
import pandas as pd
import requests
from datetime import datetime

# --- 1. SETUP & LOGIN ---
st.set_page_config(page_title="Alpha V33 Polygon", layout="wide", initial_sidebar_state="expanded")

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login_form"):
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
    if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "SPY"
    if "scan_results" not in st.session_state: st.session_state.scan_results = []

    # --- 2. SIDEBAR ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        st.subheader("🌐 Markt-Scan (Polygon.io)")
        
        min_change = st.number_input("Min. Kursänderung %", value=3.0, step=0.5)
        min_vol = st.number_input("Min. Volumen (heute)", value=200000, step=50000)
        max_price = st.number_input("Max. Preis ($)", value=30.0, step=1.0)
        
        st.divider()
        start_scan = st.button("🚀 MARKT-SCAN STARTEN", use_container_width=True, type="primary")

    # --- 3. HAUPTBEREICH ---
    st.title("⚡ Alpha Master Station: Polygon Radar")
    col_chart, col_journal = st.columns([1.8, 1])

    # --- 4. SCANNER LOGIK (POLYGON SNAPSHOT) ---
    if start_scan:
        with st.status("🔍 Snapshot von 10.000+ US-Tickern wird analysiert...", expanded=True) as status:
            # FIX: Wir nutzen jetzt den Namen aus deinem Screenshot
            poly_key = st.secrets.get("POLYGON_KEY")
            
            if not poly_key:
                st.error("❌ Fehler: 'POLYGON_KEY' wurde in den Secrets nicht gefunden!")
                st.stop()

            # Polygon Snapshot API
            url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
            
            try:
                response = requests.get(url).json()
                results = []
                
                if "tickers" in response:
                    for ticker in response.get("tickers", []):
                        sym = ticker.get("ticker")
                        chg = ticker.get("todaysChangePerc", 0)
                        vol = ticker.get("day", {}).get("v", 0)
                        last_price = ticker.get("min", {}).get("c", 0)
                        
                        if chg >= min_change and vol >= min_vol and last_price <= max_price:
                            results.append({
                                "Ticker": sym,
                                "Chg%": round(chg, 2),
                                "Vol": f"{int(vol):,}",
                                "Price": f"${last_price:.2f}",
                                "Time": datetime.now().strftime("%H:%M")
                            })
                    
                    if results:
                        st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                        st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                        status.update(label=f"✅ {len(results)} Treffer gefunden!", state="complete")
                    else:
                        st.warning("Keine Aktien mit diesen Kriterien gefunden.")
                else:
                    st.error("API Antwort fehlerhaft. Bitte Key prüfen.")
            except Exception as e:
                st.error(f"Verbindungsfehler: {e}")

    # --- 5. CHART & JOURNAL ---
    with col_chart:
        if st.session_state.scan_results:
            tickers = [r['Ticker'] for r in st.session_state.scan_results]
            st.session_state.selected_symbol = st.selectbox("🎯 Signal auswählen:", tickers)
        
        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="520" frameborder="0"></iframe>', height=520)

    with col_journal:
        st.subheader("📝 Trefferliste")
        if st.session_state.scan_results:
            st.table(pd.DataFrame(st.session_state.scan_results)[["Ticker", "Price", "Chg%"]])
        else:
            st.info("Warte auf Scan-Befehl...")

    # --- 6. FOOTER ---
    st.divider()
    st.caption(f"⚙️ Admin: Miroslav | Quelle: Polygon.io | Stand: {datetime.now().strftime('%H:%M:%S')}")