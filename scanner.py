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

    # --- 2. SIDEBAR: STRATEGIEN + SCHIEBEREGLER ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("📋 Strategie-Auswahl")
        main_strat = st.selectbox("Hauptstrategie", ["Volume Surge", "Gap Momentum", "Penny Stock Breakout"])
        extra_strat = st.selectbox("Zusatzfilter", ["Keine", "Price < $10", "Price > $10"])
        
        st.divider()
        
        st.subheader("⚙️ Manuelle Feinjustierung")
        min_vol_slider = st.number_input("Min. Volumen heute", value=300000, step=50000)
        min_chg_slider = st.slider("Min. Kursänderung %", 0.0, 50.0, 3.0, step=0.5)
        max_price_slider = st.number_input("Max. Preis ($)", value=30.0, step=1.0)
        
        st.divider()
        start_scan = st.button("🚀 STRATEGIE-SCAN STARTEN", use_container_width=True, type="primary")

    # --- 3. HAUPTBEREICH ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.5, 1])

    # --- 4. SCANNER LOGIK ---
    if start_scan:
        with st.status(f"🔍 Scanne Markt nach {main_strat}...", expanded=False) as status:
            poly_key = st.secrets.get("POLYGON_KEY")
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
                        
                        match = False
                        if main_strat == "Volume Surge" and vol > 500000: match = True
                        elif main_strat == "Gap Momentum" and chg > 5: match = True
                        elif main_strat == "Penny Stock Breakout" and last_price < 5: match = True
                            
                        if match:
                            if vol < min_vol_slider or chg < min_chg_slider or last_price > max_price_slider:
                                match = False
                        
                        if match:
                            results.append({
                                "Ticker": sym,
                                "Price": last_price,
                                "Chg%": round(chg, 2),
                                "Vol": int(vol)
                            })
                    
                    if results:
                        st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                        st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                        status.update(label="✅ Scan abgeschlossen!", state="complete")
                    else:
                        st.warning("Keine Treffer.")
            except Exception as e:
                st.error(f"Fehler: {e}")

    # --- 5. SIGNAL JOURNAL (INTERAKTIV) ---
    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            
            # KORREKTUR: selection_mode="single-row" (mit Bindestrich!)
            event = st.dataframe(
                df,
                on_select="rerun",
                selection_mode="single-row", 
                use_container_width=True,
                hide_index=True
            )
            
            # Logik für den Klick
            if event.selection and event.selection.rows:
                selected_row_index = event.selection.rows[0]
                st.session_state.selected_symbol = df.iloc[selected_row_index]["Ticker"]
        else:
            st.info("Bereit für Scan.")

    # --- 6. LIVE-CHART ---
    with col_chart:
        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="520" frameborder="0"></iframe>', height=520)

    # --- 7. TÄGLICHE ANALYSE (Nach deinen Wünschen) ---
    st.divider()
    if st.button("📊 TÄGLICHE ANALYSE ERSTELLEN"):
        if st.session_state.scan_results:
            df_analysis = pd.DataFrame(st.session_state.scan_results)
            st.subheader(f"📅 Analyse-Report vom {datetime.now().strftime('%d.%m.%Y')}")
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Top Gainer", f"{df_analysis.iloc[0]['Ticker']}", f"+{df_analysis.iloc[0]['Chg%']}%")
            c2.metric("Ø Preis", f"${df_analysis['Price'].mean():.2f}")
            c3.metric("Gesamt-Signale", f"{len(df_analysis)}")
            
            st.info(f"Strategie '{main_strat}' lieferte heute {len(df_analysis)} Treffer.")
        else:
            st.warning("Keine Daten zum Analysieren.")

    st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M')}")