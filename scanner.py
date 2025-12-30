import streamlit as st
import pandas as pd
import requests
from datetime import datetime

# --- 1. SETUP & LOGIN ---
st.set_page_config(page_title="Alpha V33 Polygon Pro", layout="wide", initial_sidebar_state="expanded")

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

    # --- 2. SIDEBAR: STRATEGIEN + MANUELLE FILTER ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        # Sektion 1: Deine Strategien
        st.subheader("📋 Strategie-Auswahl")
        main_strat = st.selectbox("Hauptstrategie", 
                                 ["Volume Surge", "Gap Momentum", "Penny Stock Breakout"])
        extra_strat = st.selectbox("Zusatzfilter", 
                                  ["Keine", "Price < $10", "Price > $10"])
        
        st.divider()
        
        # Sektion 2: Deine manuellen Schieberegler
        st.subheader("⚙️ Manuelle Feinjustierung")
        min_vol_slider = st.number_input("Min. Volumen heute", value=300000, step=50000)
        min_chg_slider = st.slider("Min. Kursänderung %", 0.0, 50.0, 3.0, step=0.5)
        max_price_slider = st.number_input("Max. Preis ($)", value=30.0, step=1.0)
        
        st.divider()
        start_scan = st.button("🚀 STRATEGIE-SCAN STARTEN", use_container_width=True, type="primary")

    # --- 3. HAUPTBEREICH ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.8, 1])

    # --- 4. SCANNER LOGIK (HYBRID: STRATEGIE + MANUELLE FILTER) ---
    if start_scan:
        with st.status(f"🔍 Scanne Markt nach {main_strat} & Filtern...", expanded=True) as status:
            # Wir nutzen deinen POLYGON_KEY aus den Secrets
            poly_key = st.secrets.get("POLYGON_KEY")
            
            if not poly_key:
                st.error("❌ Fehler: 'POLYGON_KEY' nicht gefunden!")
                st.stop()

            # Polygon Snapshot API für den gesamten US-Markt
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
                        
                        # --- FILTER-CHECK ---
                        match = False
                        
                        # 1. Check Hauptstrategie (Basis-Logik)
                        if main_strat == "Volume Surge" and vol > 500000:
                            match = True
                        elif main_strat == "Gap Momentum" and chg > 5:
                            match = True
                        elif main_strat == "Penny Stock Breakout" and last_price < 5:
                            match = True
                            
                        # 2. Check manuelle Schieberegler (Strikte Einschränkung)
                        if match:
                            if vol < min_vol_slider: match = False
                            if chg < min_chg_slider: match = False
                            if last_price > max_price_slider: match = False
                        
                        # 3. Check Zusatzfilter
                        if match:
                            if extra_strat == "Price < $10" and last_price >= 10: match = False
                            if extra_strat == "Price > $10" and last_price <= 10: match = False
                        
                        if match:
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
                        st.warning("Keine Aktien erfüllen diese Kombination aus Strategie und Filtern.")
                else:
                    st.error("API Fehler. Bitte Key oder Plan prüfen.")
            except Exception as e:
                st.error(f"Verbindungsfehler: {e}")

    # --- 5. CHART & SIGNAL JOURNAL ---
    with col_chart:
        if st.session_state.scan_results:
            tickers = [r['Ticker'] for r in st.session_state.scan_results]
            st.session_state.selected_symbol = st.selectbox("🎯 Signal zur Analyse:", tickers)
        
        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        # Stabiler Chart-Link
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="520" frameborder="0"></iframe>', height=520)

    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            st.table(pd.DataFrame(st.session_state.scan_results)[["Ticker", "Price", "Chg%"]])
        else:
            st.info("Warte auf Scan-Befehl...")

    # --- 6. FOOTER ---
    st.divider()
    st.caption(f"⚙️ Admin: Miroslav | Modus: {main_strat} + Manuelle Filter | Quelle: Polygon.io")