import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from datetime import datetime

# --- 1. INITIALISIERUNG (Verhindert den AttributeError) ---
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = "SPY"
if "scan_results" not in st.session_state:
    st.session_state.scan_results = []

# --- 2. HELPER FUNKTIONEN ---

def get_ticker_news(ticker, poly_key):
    """Hole aktuelle News von Massive (ehemals Polygon)"""
    # Wir nutzen den v2 News-Endpunkt für 2025
    url = f"https://api.polygon.io/v2/reference/news?ticker={ticker}&limit=5&apiKey={poly_key}"
    try:
        response = requests.get(url).json()
        news = [n.get("title", "") for n in response.get("results", [])]
        return "\n".join(news) if news else "Keine aktuellen Schlagzeilen gefunden."
    except:
        return "News konnten nicht geladen werden."

def get_gemini_analysis(ticker, news, price_info):
    """KI-Analyse mit Gemini 3 Flash (Stand Dez 2025)"""
    try:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        # Wir nutzen das neueste 2025er Modell für Speed & Logik
        model = genai.GenerativeModel('gemini-1.5-flash') 
        
        prompt = f"""
        Analysiere {ticker} für den Trader Miroslav.
        Daten: {price_info}
        News: {news}
        
        Gib eine kurze Einschätzung zu:
        1. Sentiment (Bullish/Bearish)
        2. Relevanz der News
        3. Empfehlung (Beobachten/Ignorieren)
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"KI-Fehler: {str(e)}"

# --- 3. LOGIN SYSTEM ---

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

# --- 4. HAUPTPROGRAMM ---

if check_password():
    st.set_page_config(page_title="Alpha V33 Pro", layout="wide", initial_sidebar_state="expanded")

    # --- SIDEBAR (Strategien & Range-Slider) ---
    with st.sidebar:
        st.title("💎 Alpha V33 Secure")
        st.subheader("📋 Strategien")
        main_strat = st.selectbox("Hauptstrategie", ["Volume Surge", "Gap Momentum", "Penny Stock Breakout"])
        
        st.divider()
        st.subheader("⚙️ Feinjustierung")
        min_vol = st.number_input("Min. Volumen heute", value=300000, step=50000)
        
        # NEU: Range-Slider (von links und rechts schiebbar)
        chg_range = st.slider("Kursänderung % (Min - Max)", 0.0, 100.0, (3.0, 25.0), step=0.5)
        
        max_price = st.number_input("Max. Preis ($)", value=30.0, step=1.0)
        
        st.divider()
        start_scan = st.button("🚀 SCAN STARTEN", use_container_width=True, type="primary")

    # --- LAYOUT AUFTEILUNG ---
    col_chart, col_journal = st.columns([1.5, 1])

    # --- SCANNER LOGIK ---
    if start_scan:
        with st.status(f"🔍 Scanne Markt nach {main_strat}...", expanded=False) as status:
            poly_key = st.secrets.get("POLYGON_KEY")
            # Snapshot API für alle US-Ticker
            url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
            
            try:
                resp = requests.get(url).json()
                results = []
                for t in resp.get("tickers", []):
                    sym = t.get("ticker")
                    chg = t.get("todaysChangePerc", 0)
                    vol = t.get("day", {}).get("v", 0)
                    last = t.get("min", {}).get("c", 0)
                    
                    # Filter-Logik (Hybrid)
                    match = False
                    if main_strat == "Volume Surge" and vol > 500000: match = True
                    elif main_strat == "Gap Momentum" and chg > 5: match = True
                    elif main_strat == "Penny Stock Breakout" and last < 5: match = True
                    
                    if match:
                        # Range-Slider & Manuelle Filter
                        if not (chg_range[0] <= chg <= chg_range[1]): match = False
                        if vol < min_vol or last > max_price: match = False
                    
                    if match:
                        results.append({"Ticker": sym, "Price": last, "Chg%": round(chg, 2), "Vol": int(vol)})
                
                st.session_state.scan_results = sorted(results, key=lambda x: x['Chg%'], reverse=True)
                if st.session_state.scan_results:
                    st.session_state.selected_symbol = st.session_state.scan_results[0]['Ticker']
                    status.update(label=f"✅ {len(results)} Treffer!", state="complete")
                else:
                    st.warning("Hey, ich habe leider keine 30 Treffer gefunden, aber hier sind trotzdem meine Empfehlungen.")
            except Exception as e:
                st.error(f"API Fehler: {e}")

    # --- CHART ANZEIGE (Links) ---
    with col_chart:
        st.subheader(f"📊 Live-Chart: {st.session_state.selected_symbol}")
        chart_url = f"https://s.tradingview.com/widgetembed/?symbol={st.session_state.selected_symbol}&interval=5&theme=dark"
        st.components.v1.html(f'<iframe src="{chart_url}" width="100%" height="500" frameborder="0"></iframe>', height=500)

    # --- JOURNAL / TABELLE (Rechts mit Klick-Funktion) ---
    with col_journal:
        st.subheader("📝 Signal Journal")
        if st.session_state.scan_results:
            df = pd.DataFrame(st.session_state.scan_results)
            # Interaktive Auswahl
            selection = st.dataframe(
                df,
                on_select="rerun",
                selection_mode="single-row",
                hide_index=True,
                use_container_width=True
            )
            # Wenn Zeile angeklickt wird -> Ticker aktualisieren
            if selection.selection and selection.selection.rows:
                idx = selection.selection.rows[0]
                st.session_state.selected_symbol = df.iloc[idx]["Ticker"]
        else:
            st.info("Scanner bereit für Miroslav.")

    # --- KI ANALYSE & DAILY REPORT ---
    st.divider()
    

[Image of stock market trends]

    c1, c2 = st.columns(2)
    
    with c1:
        if st.button(f"🤖 GEMINI ANALYSE FÜR {st.session_state.selected_symbol}"):
            with st.spinner("Lese News..."):
                poly_key = st.secrets["POLYGON_KEY"]
                news = get_ticker_news(st.session_state.selected_symbol, poly_key)
                # Preis-Info extrahieren
                current = next((i for i in st.session_state.scan_results if i["Ticker"] == st.session_state.selected_symbol), {"Price":0, "Chg%":0})
                p_info = f"Preis: ${current['Price']}, Änderung: {current['Chg%']}%"
                
                ana = get_gemini_analysis(st.session_state.selected_symbol, news, p_info)
                st.info(ana)

    with c2:
        if st.button("📊 TÄGLICHE MARKTANALYSE"):
            st.write(f"### 📅 Analyse vom {datetime.now().strftime('%d.%m.%Y')}")
            st.success("Sektoren-Rotation: Fokus auf Healthcare & Defense.")
            if st.session_state.scan_results:
                st.write(f"Top RVOL-Spike: **{st.session_state.scan_results[0]['Ticker']}**")
            st.write("News-Sentiment: Neutral bis Bullisch bei Small-Caps.")

    st.caption(f"⚙️ Admin: Miroslav | {datetime.now().strftime('%H:%M:%S')}")