import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

# --- 1. INITIALISIERUNG ---
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "SPY"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "manual_data" not in st.session_state: st.session_state.manual_data = None

# NEU: Speicher für die additiven Feinjustierungen
if "active_filters" not in st.session_state:
    st.session_state.active_filters = {} # Format: {"Name": (min, max)}

# --- 2. HELPER FUNKTIONEN ---

def get_ticker_news(ticker, poly_key):
    url = f"https://api.polygon.io/v2/reference/news?ticker={ticker}&limit=5&apiKey={poly_key}"
    try:
        response = requests.get(url).json()
        news = [n.get("title", "") for n in response.get("results", [])]
        return "\n".join(news) if news else "Keine aktuellen News."
    except: return "Fehler."

def get_single_ticker_data(ticker, poly_key):
    url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey={poly_key}"
    try:
        data = requests.get(url).json()
        if "ticker" in data:
            t = data["ticker"]
            return {"Ticker": t.get("ticker"), "Price": t.get("min", {}).get("c", 0), 
                    "Chg%": round(t.get("todaysChangePerc", 0), 2), "Vol": int(t.get("day", {}).get("v", 0))}
    except: return None

def get_gemini_analysis(ticker, news, price_info):
    try:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-1.5-flash') 
        prompt = f"Analysiere {ticker} für Miroslav. Daten: {price_info}. News: {news}. Fazit?"
        return model.generate_content(prompt).text
    except Exception as e: return f"KI-Fehler: {e}"

# --- 3. LOGIN ---

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔒 Alpha Station Login")
        with st.form("login_form"):
            pw = st.text_input("Passwort", type="password")
            if st.form_submit_button("Login"):
                if pw == st.secrets.get("PASSWORD"):
                    st.session_state["password_correct"] = True
                    st.rerun()
                else: st.error("Falsch.")
        return False
    return True

# --- 4. HAUPTPROGRAMM ---

if check_password():
    st.set_page_config(page_title="Alpha V33 Modular", layout="wide")

    with st.sidebar:
        st.title("💎 Alpha V33 Modular")
        
        # --- MODULARE FEINJUSTIERUNG ---
        st.subheader("⚙️ Feinjustierung")
        
        filter_type = st.selectbox("Filter auswählen", 
                                  ["Kursänderung %", "Volumen", "Preis min-max"])
        
        # Dynamische Schieber basierend auf Dropdown
        if filter_type == "Kursänderung %":
            val = st.slider("Bereich festlegen (%)", 0.0, 100.0, (3.0, 25.0), step=0.5)
            unit = "%"
        elif filter_type == "Volumen":
            # 0 bis 50 Milliarden (50.000.000.000)
            val = st.slider("Min. Volumen (Mio/Mrd)", 0, 50000000000, (1000000, 1000000000), step=1000000)
            unit = " Vol"
        else: # Preis
            val = st.slider("Preisbereich ($)", 0.0, 1000.0, (5.0, 50.0), step=1.0)
            unit = "$"

        if st.button(f"➕ {filter_type} hinzufügen"):
            st.session_state.active_filters[filter_type] = val
            st.success(f"{filter_type} gespeichert!")

        # Anzeige der gespeicherten Filter
        if st.session_state.active_filters:
            st.write("---")
            st.write("**Aktive Filter:**")
            to_delete = []
            for name, values in st.session_state.active_filters.items():
                col_txt, col_del = st.columns([4, 1])
                col_txt.caption(f"{name}: {values[0]} - {values[1]}{unit if name == filter_type else ''}")
                if col_del.button("❌", key=f"del_{name}"):
                    to_delete.append(name)
            
            for d in to_delete:
                del st.session_state.active_filters[d]
                st.rerun()

        st.divider()

        # 🚀 SCANNER START
        main_strat = st.selectbox("Basis-Strategie", ["Volume Surge", "Gap Momentum", "Penny Stock"])
        if st.button("🚀 SCAN STARTEN", use_container_width=True, type="primary"):
            st.session_state.manual_data = None
            with st.status("Filtere Markt...") as status:
                poly_key = st.secrets["POLYGON_KEY"]
                url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
                try:
                    resp = requests.get(url).json()
                    results = []
                    for t in resp.get("tickers", []):
                        sym, chg, vol, last = t.get("ticker"), t.get("todaysChangePerc", 0), t.get("day", {}).get("v", 0), t.get("min", {}).get("c", 0)
                        
                        # Basis-Check
                        match = True
                        
                        # Additive Filter-Logik
                        filters = st.session_state.active_filters
                        if "Kursänderung %" in filters:
                            if not (filters["Kursänderung %"][0] <= chg <= filters["Kursänderung %"][1]): match = False
                        if "Volumen" in filters:
                            if not (filters["Volumen"][0] <= vol <= filters["Volumen"][1]): match = False
                        if "Preis min-max" in filters:
                            if not (filters["Preis min-max"][0] <= last <= filters["Preis min-max"][1]): match = False
                        
                        if match:
                            results.append({"Ticker": sym, "Price": last, "Chg%": round(chg, 2), "Vol": int(vol)})
                    
                    st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                    if len(st.session_state.scan_results) < 30:
                        st.warning("Hey, ich habe leider keine 30 Treffer gefunden, aber hier sind trotzdem meine Empfehlungen.")
                    if st.session_state.scan_results: st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                    status.update(label="Scan fertig!", state="complete")
                except: st.error("Fehler")

        # 🔍 SUCHE & WATCHLIST (wie gehabt)
        st.divider()
        search_ticker = st.text_input("Ticker Suche", "").upper()
        if st.button("SUCHEN"):
            data = get_single_ticker_data(search_ticker, st.secrets["POLYGON_KEY"])
            if data:
                st.session_state.selected_symbol = search_ticker
                st.session_state.manual_data = data
        
        if st.button("⭐ IN WATCHLIST"):
            if st.session_state.selected_symbol not in st.session_state.watchlist:
                st.session_state.watchlist.append(st.session_state.selected_symbol)

    # --- HAUPTBEREICH ---
    col_chart, col_journal = st.columns([1.5, 1])

    with col_chart:
        st.subheader(f"📊 Chart: {st.session_state.selected_symbol}")
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="500" frameborder="0"></iframe>', height=500)

    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            selection = st.dataframe(df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
            if selection.selection and selection.selection.rows:
                st.session_state.selected_symbol = df.iloc[selection.selection.rows[0]]["Ticker"]
        else: st.info("Scan-Ergebnisse erscheinen hier.")

    # KI & REPORT
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button(f"✨ GEMINI ANALYSE FÜR {st.session_state.selected_symbol}"):
            with st.spinner("KI denkt nach..."):
                poly_key = st.secrets["POLYGON_KEY"]
                news = get_ticker_news(st.session_state.selected_symbol, poly_key)
                current = st.session_state.manual_data if st.session_state.manual_data else next((i for i in st.session_state.scan_results if i["Ticker"] == st.session_state.selected_symbol), {"Price":0, "Chg%":0})
                p_info = f"Preis: ${current['Price']}, Chg: {current['Chg%']}%"
                st.info(get_gemini_analysis(st.session_state.selected_symbol, news, p_info))
    
    with c2:
        if st.button("📊 DAILY ANALYSIS"):
            st.write(f"### Report {datetime.now().strftime('%d.%m.%Y')}")
            st.write("Sektoren-Rotation: Tech zu Healthcare.")
            if st.session_state.scan_results: st.success(f"Top-Pick: {st.session_state.scan_results[0]['Ticker']}")

    st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")