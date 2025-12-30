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

    # --- 2. SIDEBAR (Deine Markt-Regeln) ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        st.subheader("🌐 Globaler US-Markt Scan")
        
        # Hier definierst du, was SOPA-ähnliche Ausbrüche finden soll
        min_change = st.number_input("Min. Kursänderung %", value=5.0, step=0.5)
        min_vol = st.number_input("Min. Volumen (heute)", value=300000, step=50000)
        max_price = st.number_input("Max. Preis ($)", value=25.0, step=1.0)
        
        st.divider()
        start_scan = st.button("🚀 MARKT-SNAPSHOT STARTEN", use_container_width=True, type="primary")

    # --- 3. HAUPTBEREICH ---
    st.title("⚡ Alpha Master Station: Polygon.io Radar")
    col_chart, col_journal = st.columns([1.8, 1])

    # --- 4. SCANNER LOGIK (POLYGON SNAPSHOT) ---
    if start_scan:
        with st.status("🔍 Snapshot von 10.000+ US-Tickern wird analysiert...", expanded=True) as status:
            api_key = st.secrets.get("POLYGON_KEY")
            
            # Der Snapshot-Endpunkt für den gesamten Markt
            url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={api_key}"
            
            try:
                response = requests.get(url).json()
                results = []
                
                # Wir gehen alle Ticker durch, die Polygon liefert
                for ticker in response.get("tickers", []):
                    sym = ticker.get("ticker")
                    chg = ticker.get("todaysChangePerc", 0)
                    vol = ticker.get("day", {}).get("v", 0)
                    last_price = ticker.get("min", {}).get("c", 0)
                    
                    # Deine strikten Filterregeln
                    if chg >= min_change and vol >= min_vol and last_price <= max_price:
                        results.append({
                            "Ticker": sym,
                            "Chg%": round(chg, 2),
                            "Vol": f"{int(vol):,}",
                            "Price": f"${last_price:.2f}",
                            "Time": datetime.now().strftime("%H:%M")
                        })
                
                if results:
                    # Stärkste Ausbrüche zuerst
                    st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                    st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                    status.update(label=f"✅ {len(results)} Treffer im Markt gefunden!", state="complete", expanded=False)
                else:
                    st.warning("Keine Aktien mit diesen Kriterien gefunden.")
            except Exception as e:
                st.error(f"Fehler bei Polygon-Abfrage: {e}")

    # --- 5. CHART & SIGNAL JOURNAL ---
    with col_chart:
        if st.session_state.scan_results:
            tickers = [r['Ticker'] for r in st.session_state.scan_results]
            st.session_state.selected_symbol = st.selectbox("🎯 Signal zur Analyse wählen:", tickers)
        
        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        # Stabiles Widget (Symbol wird dynamisch aus Polygon-Daten geladen)
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="520" frameborder="0"></iframe>', height=520)

    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            st.table(pd.DataFrame(st.session_state.scan_results))
        else:
            st.info("Warte auf Miroslavs Startbefehl...")

    # --- 6. FOOTER ---
    st.divider()
    st.caption(f"⚙️ Admin-Modus: Miroslav | Datenquelle: Polygon.io Snapshot | {datetime.now().strftime('%H:%M:%S')}")