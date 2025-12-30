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

    # --- 2. SIDEBAR: STRATEGIEN + RANGE-SLIDER ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        
        st.subheader("📋 Strategien")
        main_strat = st.selectbox("Hauptstrategie", ["Volume Surge", "Gap Momentum", "Penny Stock Breakout"])
        extra_strat = st.selectbox("Zusatzfilter", ["Keine", "Price < $10", "Price > $10"])
        
        st.divider()
        
        st.subheader("⚙️ Feinjustierung")
        min_vol_slider = st.number_input("Min. Volumen heute", value=300000, step=50000)
        
        # HIER IST DER NEUE RANGE-SLIDER (Zwei Schieber auf einem Regler)
        # Er gibt ein Tupel zurück, z.B. (3.0, 20.0)
        chg_range = st.slider(
            "Kursänderung % (Min bis Max)", 
            0.0, 100.0, (3.0, 25.0), step=0.5
        )
        
        max_price_slider = st.number_input("Max. Preis ($)", value=30.0, step=1.0)
        
        st.divider()
        start_scan = st.button("🚀 SCAN STARTEN", use_container_width=True, type="primary")

    # --- 3. HAUPTBEREICH ---
    st.title("⚡ Alpha Master Station: Live Radar")
    col_chart, col_journal = st.columns([1.5, 1])

    # --- 4. SCANNER LOGIK (POLYGON SNAPSHOT) ---
    if start_scan:
        with st.status(f"🔍 Scanne Markt nach {main_strat}...", expanded=False) as status:
            poly_key = st.secrets.get("POLYGON_KEY") # Nutzt POLYGON_KEY aus deinen Secrets
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
                            # FILTER MIT RANGE-SLIDER: Prüfe ob chg zwischen Min und Max liegt
                            if not (chg_range[0] <= chg <= chg_range[1]): match = False
                            if vol < min_vol_slider or last_price > max_price_slider: match = False
                        
                        if match:
                            if extra_strat == "Price < $10" and last_price >= 10: match = False
                            if extra_strat == "Price > $10" and last_price <= 10: match = False

                        if match:
                            results.append({"Ticker": sym, "Price": last_price, "Chg%": round(chg, 2), "Vol": int(vol)})
                    
                    if results:
                        st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                        st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                        status.update(label=f"✅ {len(results)} Treffer gefunden!", state="complete")
                    else:
                        # Hinweis gemäß deinen Anforderungen, falls keine 30 Spiele/Aktien gefunden werden
                        st.warning("Hey, ich habe leider keine 30 Treffer gefunden, aber hier sind trotzdem meine Empfehlungen.")
            except Exception as e:
                st.error(f"Fehler: {e}")

    # --- 5. SIGNAL JOURNAL (INTERAKTIV MIT KLICK-FUNKTION) ---
    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            # Nutzt selection_mode="single-row" für die Klick-Funktionalität
            event = st.dataframe(
                df,
                on_select="rerun",
                selection_mode="single-row",
                use_container_width=True,
                hide_index=True
            )
            
            if event.selection and event.selection.rows:
                selected_row = event.selection.rows[0]
                st.session_state.selected_symbol = df.iloc[selected_row]["Ticker"]
        else:
            st.info("Scanner bereit.")

    # --- 6. LIVE-CHART ---
    with col_chart:
        st.subheader(f"📊 Chart: {st.session_state.selected_symbol}")
        # Stabiles TradingView Widget
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="520" frameborder="0"></iframe>', height=520)

    # --- 7. TÄGLICHE ANALYSE (Inklusive aller Vorschläge) ---
    st.divider()
    if st.button("📊 TÄGLICHE ANALYSE ERSTELLEN"):
        if st.session_state.scan_results:
            df_ana = pd.DataFrame(st.session_state.scan_results)
            st.subheader(f"📅 Analyse-Report vom {datetime.now().strftime('%d.%m.%Y')}")
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Top-Gainer", f"{df_ana.iloc[0]['Ticker']}", f"+{df_ana.iloc[0]['Chg%']}%")
            c2.metric("Ø Preis der Signale", f"${df_ana['Price'].mean():.2f}")
            c3.metric("Anzahl Signale", f"{len(df_ana)}")
            
            st.write("---")
            st.write("**Zusammenfassung der Strategie-Performance:**")
            st.write(f"Die Strategie '{main_strat}' hat heute den Markt nach Aktien zwischen {chg_range[0]}% und {chg_range[1]}% Veränderung durchsucht.")
            st.write(f"Das durchschnittliche Volumen der gefundenen Ticker lag bei {int(df_ana['Vol'].mean()):,} Shares.")
        else:
            st.warning("Keine Daten für eine Analyse vorhanden.")

    st.caption(f"⚙️ Admin: Miroslav | Daten: Polygon.io (15m verzögert)")