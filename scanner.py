import streamlit as st
import google.generativeai as genai
from datetime import datetime

# --- GEMINI AI INTEGRATION ---

def get_gemini_analysis(ticker, news, price_info):
    """Nutzt Gemini 1.5 Flash für die Analyse [2025 Standard]"""
    # API-Key aus den Secrets laden
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    # Modell auswählen (Flash ist perfekt für schnelle Trading-Checks)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    Du bist ein Profi-Aktienanalyst für Miroslavs Alpha Station. 
    Analysiere den Ticker: {ticker}
    Aktuelle Daten (15m verzögert): {price_info}
    Top News-Headlines:
    {news}
    
    Erstelle eine kurze, ehrliche Einschätzung für einen Daytrader:
    - Sentiment: Ist die Stimmung positiv oder negativ?
    - News-Check: Sind die Nachrichten relevant für den heutigen Move?
    - Fazit: Worauf muss Miroslav heute bei dieser Aktie achten?
    Vermeide Halluzinationen. Wenn Daten fehlen, sag es einfach.
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"KI-Fehler: {str(e)}"

# --- INTEGRATION IN DEN HAUPTBEREICH ---
# (Füge dies unter deinen Chart-Bereich ein)

st.divider()
st.subheader(f"🤖 Gemini AI Live-Analyse: {st.session_state.selected_symbol}")

if st.button(f"✨ MEINUNG VON GEMINI ZU {st.session_state.selected_symbol}"):
    with st.spinner("Ich lese die News und analysiere den Ticker für dich..."):
        # Nutzt deine bestehenden News-Funktionen von Polygon
        poly_key = st.secrets["POLYGON_KEY"]
        ticker = st.session_state.selected_symbol
        
        # News abrufen (Funktion aus dem vorherigen Schritt)
        news_data = get_ticker_news(ticker, poly_key) 
        
        # Aktuelle Preisdaten aus deinem Scan-Ergebnis
        current_data = next((item for item in st.session_state.scan_results if item["Ticker"] == ticker), None)
        price_info = f"Preis: {current_data['Price']}, Chg: {current_data['Chg%']}%" if current_data else "Keine Preisdaten"
        
        # Die Analyse starten
        analysis_result = get_gemini_analysis(ticker, news_data, price_info)
        
        st.markdown(f"> **Analyse:** \n\n{analysis_result}")