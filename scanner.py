import streamlit as st
import pandas as pd
import requests
import anthropic
from datetime import datetime

# 1. INITIALISIERUNG
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = "BTC"
if "scan_results" not in st.session_state:
    st.session_state.scan_results = []
if "active_filters" not in st.session_state:
    st.session_state.active_filters = {}

def apply_presets(strat_name, market_type):
    presets = {
        "Volume Surge": {"RVOL": (2.0, 50.0), "Kursaenderung %": (0.5, 30.0)},
        "Bull Flag": {"Vortag %": (4.0, 25.0), "Kursaenderung %": (-1.5, 1.5), "RVOL": (1.0, 50.0)},
        "Penny Stock": {"Preis": (0.0001, 5.0), "RVOL": (1.5, 50.0)},
        "Unusual Volume": {"RVOL": (5.0, 100.0)}
    }
    if strat_name in presets:
        st.session_state.active_filters = presets[strat_name].copy()

st.set_page_config(page_title="Alpha V46 Claude Pro", layout="wide")

# SIDEBAR: UNIFIED STRATEGY & FILTER
with st.sidebar:
    st.title("💎 Alpha V46 Claude")
    m_type = st.radio("Markt:", ["Krypto", "Aktien"], horizontal=True)
    strat = st.selectbox("Strategie:", ["Volume Surge", "Bull Flag", "Penny Stock", "Unusual Volume"])
    
    if st.button("➕ Filter laden"):
        apply_presets(strat, m_type)
        st.rerun()

    if st.session_state.active_filters:
        for n, v in list(st.session_state.active_filters.items()):
            st.session_state.active_filters[n] = st.slider(
                f"{n}", -100.0, 100.0, (float(v[0]), float(v[1])), key=f"s_{n}"
            )
    
    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        with st.status("Hole Live-Daten...") as status:
            poly_key = st.secrets["POLYGON_KEY"]
            url = f"https://api.polygon.io/v2/snapshot/locale/{'global' if m_type=='Krypto' else 'us'}/markets/{'crypto' if m_type=='Krypto' else 'stocks'}/tickers?apiKey={poly_key}"
            try:
                resp = requests.get(url).json()
                tickers = resp.get("tickers", [])
                res = []
                for t in tickers:
                    d, prev, last = t.get("day", {}), t.get("prevDay", {}), t.get("lastTrade", {})
                    price = last.get("p") or d.get("c") or t.get("min", {}).get("c") or prev.get("c") or 0
                    if price <= 0:
                        continue
                    
                    chg = t.get("todaysChangePerc", 0)
                    vol = d.get("v") or last.get("v") or 1
                    prev_vol = prev.get("v", 1) or 1
                    rvol = round(vol / prev_vol, 2)
                    vortag_chg = round(((prev.get("c", 0) - prev.get("o", 0)) / (prev.get("o", 1) or 1)) * 100, 2)
                    
                    # Filter-Logik
                    match = True
                    f = st.session_state.active_filters
                    if f:
                        if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]):
                            match = False
                        if "Kursaenderung %" in f and not (f["Kursaenderung %"][0] <= chg <= f["Kursaenderung %"][1]):
                            match = False
                        if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]):
                            match = False
                        if "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]):
                            match = False
                    
                    if match:
                        # Krypto-Ticker bereinigen: X:BTCUSD -> BTC
                        ticker_clean = t.get("ticker", "").replace("X:", "").replace("USD", "")
                        res.append({
                            "Ticker": ticker_clean,
                            "Price": round(price, 6),
                            "Chg%": round(chg, 2),
                            "RVOL": rvol,
                            "Vortag%": vortag_chg
                        })
                
                # Sortierung nach RVOL (höchstes zuerst)
                st.session_state.scan_results = sorted(res, key=lambda x: x['RVOL'], reverse=True)[:50]
                status.update(label=f"Scan fertig: {len(st.session_state.scan_results)} Signale", state="complete")
            except Exception as e:
                st.error(f"API Fehler: {e}")

# HAUPTBEREICH
c_chart, c_journal = st.columns([2, 1])

with c_journal:
    st.subheader("📋 Live Journal")
    if st.session_state.scan_results:
        df = pd.DataFrame(st.session_state.scan_results)
        sel = st.dataframe(
            df,
            on_select="rerun",
            selection_mode="single-row",
            hide_index=True,
            use_container_width=True
        )
        if sel.selection and sel.selection.rows:
            row = df.iloc[sel.selection.rows[0]]
            st.session_state.selected_symbol = str(row["Ticker"])
            st.session_state.current_data = row.to_dict()

with c_chart:
    st.subheader(f"📊 {st.session_state.selected_symbol} (4H Ansicht)")
    # TradingView Symbol: Krypto braucht BINANCE:XXXUSDT Format
    tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if m_type == "Krypto" else st.session_state.selected_symbol
    tv_html = f'''
    <div style="height:550px;">
        <div id="tv" style="height:100%"></div>
        <script src="https://s3.tradingview.com/tv.js"></script>
        <script>
            new TradingView.widget({{
                "autosize": true,
                "symbol": "{tv_sym}",
                "interval": "240",
                "theme": "dark",
                "style": "1",
                "container_id": "tv"
            }});
        </script>
    </div>
    '''
    st.components.v1.html(tv_html, height=550)

# --- CLAUDE KI-ANALYSE ---
st.divider()
if st.button("🤖 CLAUDE PROFI-ANALYSE"):
    if "current_data" in st.session_state:
        with st.spinner("Claude 3.5 Sonnet analysiert..."):
            try:
                poly_key = st.secrets["POLYGON_KEY"]
                ticker = st.session_state.selected_symbol
                news_resp = requests.get(
                    f"https://api.polygon.io/v2/reference/news?ticker={ticker}&limit=3&apiKey={poly_key}"
                ).json()
                news_txt = "\n".join([f"- {n['title']}" for n in news_resp.get("results", [])])
                if not news_txt:
                    news_txt = "Keine aktuellen News verfügbar."

                client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                d = st.session_state.current_data
                
                prompt = f"""Du bist ein technisches Trading-Terminal. Analysiere {d['Ticker']} basierend auf diesen FAKTEN:
- Preis: {d['Price']}
- RVOL: {d['RVOL']}
- Tagesänderung: {d['Chg%']}%
- Vortag: {d['Vortag%']}%

News:
{news_txt}

Aufgabe:
1. Nenne exakte Support/Resistance Marken (basierend auf runden Zahlen und psychologischen Leveln)
2. Berechne den Alpha-Score: (RVOL * 12) + (|Vortag%| * 10) + (|Chg%| * 8)
3. Gib ein Rating von 1-100 für die Trade-Qualität
4. Klare Empfehlung: LONG, SHORT oder ABWARTEN

Keine Ausreden, keine Risikowarnungen. Nur Fakten und Zahlen."""

                message = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    system="Du bist ein präzises Finanz-Terminal für professionelle Trader. Keine Höflichkeitsfloskeln, keine Disclaimers. Nur technische Analyse und konkrete Zahlen.",
                    messages=[{"role": "user", "content": prompt}]
                )
                st.markdown(f"### 🛡️ Claude Report: {d['Ticker']}")
                st.write(message.content[0].text)
            except Exception as e:
                st.error(f"Claude API Fehler: {e}")
    else:
        st.warning("Wähle erst einen Ticker aus dem Journal aus.")