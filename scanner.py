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
    # Initialisierung der Zustände
    if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "SPY"
    if "scan_results" not in st.session_state: st.session_state.scan_results = []

    # --- 2. SIDEBAR: STRATEGIEN + MANUELLE FILTER ---
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

    # --- 3. HAUPTBEREICH (Layout) ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.5, 1])

    # --- 4. SCANNER LOGIK (POLYGON SNAPSHOT) ---
    if start_scan:
        with st.status(f"🔍 Durchsuche Markt nach {main_strat}...", expanded=False) as status:
            # Nutzt deinen POLYGON_KEY aus den Secrets
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
                        
                        # Hybrid-Filter Logik
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
                        st.warning("Keine Treffer gefunden.")
            except Exception as e:
                st.error(f"Fehler: {e}")

    # --- 5. SIGNAL JOURNAL MIT KLICK-FUNKTION (GELBER BEREICH) ---
    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            
            # Interaktive Tabelle: Ermöglicht das Anklicken einer Zeile
            event = st.dataframe(
                df,
                on_select="rerun",
                selection_mode="single_row",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Price": st.column_config.NumberColumn(format="$%.2f"),
                    "Vol": st.column_config.NumberColumn(format="%d"),
                    "Chg%": st.column_config.NumberColumn(format="%.2f%%")
                }
            )
            
            # Wenn eine Zeile angeklickt wird, ändere das Symbol für den Chart
            if len(event.selection.rows) > 0:
                selected_row_index = event.selection.rows[0]
                st.session_state.selected_symbol = df.iloc[selected_row_index]["Ticker"]
        else:
            st.info("Scanner bereit für Miroslav.")

    # --- 6. LIVE-CHART ANZEIGE (GRÜNER BEREICH) ---
    with col_chart:
        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        # Chart wird automatisch aktualisiert, wenn oben in der Tabelle geklickt wird
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="520" frameborder="0"></iframe>', height=520)

    # --- 7. TÄGLICHE ANALYSE ---
    st.divider()
    if st.button("📊 TÄGLICHE ANALYSE ERSTELLEN"):
        if st.session_state.scan_results:
            top_stock = st.session_state.scan_results[0]
            st.success(f"Top-Signal des Tages: {top_stock['Ticker']} mit {top_stock['Chg%']}% Plus!")
            st.write(f"Durchschnittlicher Preis der Signale: ${df['Price'].mean():.2f}")
        else:
            st.warning("Keine Daten für die Analyse vorhanden.")

    st.caption(f"⚙️ Admin: Miroslav | Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}")