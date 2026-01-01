import streamlit as st
import pandas as pd
import requests
from openai import OpenAI
from datetime import datetime

# 1. INITIALISIERUNG
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "BTC"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}
if "last_strat" not in st.session_state: st.session_state.last_strat = ""

def apply_presets(strat_name, market_type):
    # Alle 13 Profi-Strategien
    presets = {
        "Volume Surge": {"RVOL": (2.0, 50.0), "Kursänderung %": (0.5, 30.0)},
        "Gap Momentum": {"Gap %": (2.5, 25.0), "RVOL": (1.2, 50.0)},
        "Penny Stock": {"Preis": (0.0001, 5.0), "RVOL": (1.5, 50.0)},
        "Bull Flag": {"Vortag %": (4.0, 25.0), "Kursänderung %": (-1.5, 1.5), "RVOL": (1.0, 50.0)},
        "Unusual Volume": {"RVOL": (5.0, 100.0)},
        "High of Day (HOD)": {"Kursänderung %": (2.0, 30.0), "RVOL": (1.3, 50.0)},
        "Short Squeeze": {"Kursänderung %": (8.0, 50.0), "RVOL": (3.0, 100.0)},
        "Low Float": {"Kursänderung %": (10.0, 100.0)},
        "Blue Chip Pullback": {"Kursänderung %": (-5.0, -0.5)},
        "Multi-Day Runner": {"Vortag %": (3.0, 20.0), "Kursänderung %": (2.0, 20.0)},
        "Pre-Market Gapper": {"Gap %": (3.0, 40.0)},
        "Dead Cat Bounce": {"Vortag %": (-40.0, -10.0), "Kursänderung %": (1.0, 10.0)},
        "Golden Cross Proxy": {"SMA Trend %": (0.5, 15.0)}
    }
    if strat_name in presets:
        st.session_state.active_filters = presets[strat_name].copy()

st.set_page_config(page_title="Alpha V38 Master", layout="wide")

# SIDEBAR: UNIFIED STRATEGY & FILTER
with st.sidebar:
    st.title("💎 Alpha V38 Master")
    m_type = st.radio("Markt:", ["Aktien", "Krypto"], horizontal=True)
    
    st.divider()
    strat_list = ["Volume Surge", "Gap Momentum", "Penny Stock", "Bull Flag", "Unusual Volume", "High of Day (HOD)", "Short Squeeze", "Low Float", "Blue Chip Pullback", "Multi-Day Runner", "Pre-Market Gapper", "Dead Cat Bounce", "Golden Cross Proxy"]
    selected_strat = st.selectbox("Strategie & Filter:", strat_list)
    
    if selected_strat != st.session_state.last_strat:
        apply_presets(selected_strat, m_type)
        st.session_state.last_strat = selected_strat

    st.subheader("⚙️ Feinjustierung")
    if st.session_state.active_filters:
        for name, values in list(st.session_state.active_filters.items()):
            new_val = st.slider(f"{name}", -100.0, 100.0, (float(values[0]), float(values[1])), key=f"sl_{name}")
            st.session_state.active_filters[name] = new_val
    
    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        with st.status("Suche Signale...") as status:
            poly_key = st.secrets["POLYGON_KEY"]
            url = f"https://api.polygon.io/v2/snapshot/locale/{'global' if m_type=='Krypto' else 'us'}/markets/{'crypto' if m_type=='Krypto' else 'stocks'}/tickers?apiKey={poly_key}"
            try:
                resp = requests.get(url).json()
                res = []
                for t in resp.get("tickers", []):
                    d_d = t.get("day", {})
                    price = d_d.get("c") or t.get("lastTrade", {}).get("p") or 0
                    if price <= 0: continue
                    chg, vol, prev = t.get("todaysChangePerc", 0), d_d.get("v", 1), t.get("prevDay", {})
                    prev_c = prev.get("c") or price
                    rvol = round(vol / (prev.get("v", 1) or 1), 2)
                    vortag_chg = round(((prev_c - (prev.get("o") or prev_c)) / (prev.get("o") or 1)) * 100, 2)
                    sma_trend = round(((price - prev_c) / prev_c) * 100, 2)
                    
                    match = True
                    f = st.session_state.active_filters
                    if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                    if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                    if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]): match = False
                    
                    if match:
                        score = min(100, int((rvol * 12) + (abs(sma_trend) * 10) + (abs(chg) * 8)))
                        res.append({"Ticker": t.get("ticker").replace("X:", ""), "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "Vortag%": vortag_chg, "Alpha": score})
                
                st.session_state.scan_results = sorted(res, key=lambda x: x['Alpha'], reverse=True)
                if len(st.session_state.scan_results) < 30:
                    st.warning("Hey, ich habe leider keine 30 Spiele gefunden, aber hier sind trotzdem meine Empfehlungen. [cite: 2025-12-28]")
                status.update(label=f"Scan fertig: {len(res)} Treffer", state="complete")
            except Exception as e: st.error(f"Fehler: {e}")

# HAUPTBEREICH
c_chart, c_journal = st.columns([2, 1])
with c_journal:
    st.subheader("📝 Live Journal")
    if st.session_state.scan_results:
        df_res = pd.DataFrame(st.session_state.scan_results)
        sel = st.dataframe(df_res, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
        if sel.selection and sel.selection.rows:
            row = df_res.iloc[sel.selection.rows[0]]
            st.session_state.selected_symbol = str(row["Ticker"])
            st.session_state.current_data = row.to_dict()

with c_chart:
    st.subheader(f"📊 {st.session_state.selected_symbol} (4H Ansicht)")
    tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if m_type == "Krypto" else st.session_state.selected_symbol
    st.components.v1.html(f'''
        <div style="height:600px;"><div id="tv" style="height:100%"></div>
        <script src="https://s3.tradingview.com/tv.js"></script>
        <script>new TradingView.widget({{"autosize": true, "symbol": "{tv_sym}", "interval": "240", "theme": "dark", "style": "1", "locale": "de", "container_id": "tv"}});</script></div>
    ''', height=600)

# KI ANALYSE: "NO EXCUSES" PROMPT
st.divider()
if st.button("🤖 VOLLANALYSE STARTEN"):
    if "current_data" in st.session_state:
        with st.spinner("Analysiere News und Chart-Marken..."):
            try:
                poly_key = st.secrets["POLYGON_KEY"]
                news_resp = requests.get(f"https://api.polygon.io/v2/reference/news?ticker={st.session_state.selected_symbol}&limit=3&apiKey={poly_key}").json()
                news_txt = "\n".join([f"- {n['title']}" for n in news_resp.get("results", [])])

                client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
                d = st.session_state.current_data
                
                prompt = f\"\"\"
                DU BIST EIN SENIOR HEDGEFONDS-ANALYST. 
                DATEN (FAKT): Ticker {d['Ticker']}, Preis {d['Price']}, RVOL {d['RVOL']}, Change {d['Chg%']}%, Vortag {d['Vortag%']}%.
                NEWS: {news_txt}

                AUFGABE:
                1. ANALYSE: Ist das Setup (z.B. Bull-Flag) basierend auf RVOL und Vortag valide?
                2. NEWS-FAZIT: Wie beeinflusst die Nachrichtenlage den Trade?
                3. CHART-MARKEN: Nenne 2 Support- und 2 Resistance-Zahlen.
                4. TREND: Wo verläuft die primäre Linie?
                5. HANDLUNG: Kauf-Limit und Stop-Loss setzen.
                6. KI-RATING: 1-100 Punkte.
                KEINE AUSREDEN ÜBER FEHLENDE ECHTZEITDATEN. NUTZE DIE OBIGEN WERTE!
                \"\"\"
                
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "system", "content": "Du bist ein präziser Finanz-Analyst. Keine Floskeln, nur knallharte Daten-Auswertung."},
                              {"role": "user", "content": prompt}]
                )
                st.markdown(f"### 🛡️ Professional Report: {d['Ticker']}")
                st.write(response.choices[0].message.content)
            except Exception as e: st.error(f"KI Fehler: {e}")
    else: st.warning("Ticker auswählen!")