import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime

# --- 1. SETUP & LOGIN ---
st.set_page_config(page_title="Alpha V33 Secure", layout="wide", initial_sidebar_state="expanded")

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
    # Session States für Miroslavs Terminal
    if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "SPY"
    if "scan_results" not in st.session_state: st.session_state.scan_results = []

    # --- 2. SIDEBAR ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        st.subheader("Strategie-Filter")
        main_strat = st.selectbox("Nur nach dieser Strategie suchen:", 
                                 ["Volume Surge", "Gap Momentum", "RSI Breakout"])
        extra_strat = st.selectbox("Zusatzfilter (Strikt)", 
                                  ["Keine", "Penny Stocks (< $10)", "Mid-Cap Focus"])
        
        st.divider()
        include_prepost = st.checkbox("🌙 Pre & Post Market", value=True)
        
        start_scan = st.button("🚀 STRATEGIE-SCAN STARTEN", use_container_width=True, type="primary")

    # --- 3. HAUPTBEREICH (Layout) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.8, 1])

    # --- 4. SCANNER LOGIK (FMP API) ---
    if start_scan:
        with st.status("🔍 Miroslavs Terminal kontaktiert FMP Server...", expanded=True) as status:
            # Key säubern (entfernt eventuelle Reste von Fehlformatierungen)
            api_key = str(st.secrets.get("API_KEY", "")).strip()
            
            # API Endpunkt wählen
            if main_strat == "Gap Momentum":
                url = f"https://financialmodelingprep.com/api/v3/stock_market/gainers?apikey={api_key}"
            else:
                # 'actives' findet automatisch Volumenspikes wie SOPA
                url = f"https://financialmodelingprep.com/api/v3/stock_market/actives?apikey={api_key}"
            
            try:
                # Request mit User-Agent Header, um 403-Sperren zu umgehen
                res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
                
                if res.status_code == 403:
                    st.error("❌ API Fehler 403: FMP lehnt den Key ab. Bitte prüfe, ob dein Abo aktiv ist.")
                    st.stop()
                
                data = res.json()
                results = []
                
                if isinstance(data, list):
                    for stock in data[:40]:
                        sym = stock.get("symbol")
                        chg = stock.get("changesPercentage", 0)
                        prc = stock.get("price", 0)
                        
                        # Filter-Logik
                        match = True
                        if extra_strat == "Penny Stocks (< $10)" and prc >= 10: match = False
                        
                        if match:
                            results.append({
                                "Ticker": sym, 
                                "Price": f"${prc:.2f}", 
                                "Chg%": chg, 
                                "Time": datetime.now().strftime("%H:%M")
                            })
                
                if results:
                    st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                    # Ersten Treffer (z.B. SOPA) automatisch in den Fokus rücken
                    st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                    status.update(label=f"✅ {len(results)} Treffer gefunden!", state="complete", expanded=False)
                else:
                    st.warning("Keine Treffer für diese Auswahl.")
            except Exception as e:
                st.error(f"Verbindungsfehler: {e}")

    # --- 5. DYNAMISCHER CHART ---
    with col_chart:
        if st.session_state.scan_results:
            ticker_list = [r['Ticker'] for r in st.session_state.scan_results]
            st.session_state.selected_symbol = st.selectbox("🎯 Welchen Treffer anzeigen?", ticker_list)

        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        # Stabiler TradingView Chart ohne CBOE-Präfix-Fehler
        chart_code = f"""
            <iframe src="https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark" 
            width="100%" height="520" frameborder="0" allowtransparency="true" scrolling="no"></iframe>
        """
        st.components.v1.html(chart_code, height=520)

    # --- 6. SIGNAL JOURNAL ---
    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            df['Chg%'] = df['Chg%'].apply(lambda x: f"{x:+.2f}%")
            st.table(df)
        else:
            st.info("Scanner bereit. Starte den Scan für Miroslavs Strategie.")

    # --- 7. FOOTER ---
    st.divider()
    f1, f2, f3 = st.columns(3)
    with f1: st.caption("📍 8500 Gerlikon | Landhaus Terminal")
    with f2: st.caption(f"⚙️ **Admin-Modus:** Miroslav | Strategie: {main_strat}")
    with f3: st.caption(f"🕒 Stand: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")