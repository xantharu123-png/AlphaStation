import os
import subprocess
import traceback
import ctypes
import sys
from datetime import datetime

def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

# --- DER VOLLSTÄNDIGE MASTER-CODE V42 ---
scanner_code = r"""
import streamlit as st
import pandas as pd
import requests
from openai import OpenAI
from datetime import datetime

# 1. INITIALISIERUNG
if "selected_symbol" not in st.session_state: st.session_state.selected_symbol = "BTC"
if "scan_results" not in st.session_state: st.session_state.scan_results = []
if "active_filters" not in st.session_state: st.session_state.active_filters = {}

def apply_presets(strat_name, market_type):
    presets = {
        "Volume Surge": {"RVOL": (2.0, 50.0), "Kursänderung %": (0.5, 30.0)},
        "Bull Flag": {"Vortag %": (4.0, 25.0), "Kursänderung %": (-1.5, 1.5), "RVOL": (1.0, 50.0)},
        "Penny Stock": {"Preis": (0.0001, 5.0), "RVOL": (1.5, 50.0)},
        "Unusual Volume": {"RVOL": (5.0, 100.0)}
    }
    if strat_name in presets:
        st.session_state.active_filters = presets[strat_name].copy()

st.set_page_config(page_title="Alpha V42 Master", layout="wide")

# SIDEBAR
with st.sidebar:
    st.title("💎 Alpha V42 Master")
    m_type = st.radio("Märkte:", ["Krypto", "Aktien"], horizontal=True)
    strat = st.selectbox("Strategie:", ["Volume Surge", "Bull Flag", "Penny Stock", "Unusual Volume"])
    
    if st.button("➕ Filter laden"):
        apply_presets(strat, m_type); st.rerun()

    if st.session_state.active_filters:
        for n, v in list(st.session_state.active_filters.items()):
            st.session_state.active_filters[n] = st.slider(f"{n}", -100.0, 100.0, (float(v[0]), float(v[1])), key=f"s_{n}")
    
    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        with st.status("Hole Live-Daten...") as status:
            poly_key = st.secrets["POLYGON_KEY"]
            url = f"https://api.polygon.io/v2/snapshot/locale/{'global' if m_type=='Krypto' else 'us'}/markets/{'crypto' if m_type=='Krypto' else 'stocks'}/tickers?apiKey={poly_key}"
            try:
                resp = requests.get(url).json()
                tickers = resp.get("tickers", [])
                res = []
                for t in tickers:
                    # ROBUSTER PREIS-EXTRAKTOR
                    d = t.get("day", {})
                    prev = t.get("prevDay", {})
                    price = t.get("lastTrade", {}).get("p") or d.get("c") or t.get("min", {}).get("c") or prev.get("c") or 0
                    
                    if price <= 0: continue
                    
                    chg = t.get("todaysChangePerc", 0)
                    vol = d.get("v") or t.get("lastTrade", {}).get("v") or 1
                    rvol = round(vol / (prev.get("v", 1) or 1), 2)
                    vortag_chg = round(((prev.get("c", 0) - prev.get("o", 0)) / (prev.get("o", 1) or 1)) * 100, 2)
                    
                    # FILTER-LOGIK (Nur wenn Filter gesetzt sind)
                    match = True
                    f = st.session_state.active_filters
                    if f:
                        if "RVOL" in f and not (f["RVOL"][0] <= rvol <= f["RVOL"][1]): match = False
                        if "Kursänderung %" in f and not (f["Kursänderung %"][0] <= chg <= f["Kursänderung %"][1]): match = False
                    
                    if match:
                        res.append({"Ticker": t.get("ticker").replace("X:", "").replace("USD", ""), "Price": price, "Chg%": round(chg, 2), "RVOL": rvol, "Vortag%": vortag_chg})
                
                st.session_state.scan_results = sorted(res, key=lambda x: x['RVOL'], reverse=True)[:50]
                status.update(label=f"Scan fertig: {len(res)} Coins gefunden", state="complete")
            except Exception as e: st.error(f"API Fehler: {e}")

# HAUPTBEREICH
c_chart, c_journal = st.columns([2, 1])
with c_journal:
    st.subheader("📝 Journal")
    if st.session_state.scan_results:
        df = pd.DataFrame(st.session_state.scan_results)
        sel = st.dataframe(df, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True)
        if sel.selection and sel.selection.rows:
            row = df.iloc[sel.selection.rows[0]]
            st.session_state.selected_symbol = str(row["Ticker"])
            st.session_state.current_data = row.to_dict()

with c_chart:
    st.subheader(f"📊 {st.session_state.selected_symbol} (4H)")
    tv_sym = f"BINANCE:{st.session_state.selected_symbol}USDT" if m_type == "Krypto" else st.session_state.selected_symbol
    st.components.v1.html(f'<div style="height:550px;"><div id="tv" style="height:100%"></div><script src="https://s3.tradingview.com/tv.js"></script><script>new TradingView.widget({{"autosize": true, "symbol": "{tv_sym}", "interval": "240", "theme": "dark", "style": "1", "container_id": "tv"}});</script></div>', height=550)

# --- DER "MAULKORB-PROMPT" FÜR DIE KI ---
st.divider()
if st.button("🤖 PROFI-ANALYSE ERZWINGEN"):
    if "current_data" in st.session_state:
        with st.spinner("Extrahiere Chart-Marken..."):
            try:
                client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
                d = st.session_state.current_data
                
                # Wir schicken nur Daten und eine strikte Anweisung
                sys_msg = "Du bist ein Terminal. Antworte NUR im Analyse-Format. Verbote: 'Keine Echtzeitdaten', 'Ich kann nicht', 'Bitcoin ist riskant'."
                prompt = f\"\"\"
                DATENSATZ: Ticker={d['Ticker']}, Preis={d['Price']}, RVOL={d['RVOL']}, Change={d['Chg%']}%.
                
                AUFGABE:
                1. TRENDLINIEN: Wo liegen sie aktuell?
                2. SUPPORT/RESISTANCE: Nenne 2 Level basierend auf {d['Price']}.
                3. NEWS: (Suche intern nach {d['Ticker']}).
                4. RATING: 1-100.
                REAGIERE SOFORT OHNE EINLEITUNG.
                \"\"\"
                
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": prompt}],
                    temperature=0.2 # Macht die KI weniger "gesprächig"
                )
                st.markdown(f"### 🛡️ Analyst Report: {d['Ticker']}")
                st.write(response.choices[0].message.content)
            except Exception as e: st.error(f"KI Fehler: {e}")
"""

def main():
    if not is_admin():
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
        return
    os.chdir(r"C:\Users\miros\Desktop\TradingBot")
    try:
        print("🧊 Eisbrecher V42: Bereinige GitHub...")
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "stash"], check=True)
        subprocess.run(["git", "pull", "origin", "main", "--rebase"], check=True)
        
        with open("scanner.py", "w", encoding="utf-8") as f: f.write(scanner_code.strip())
        subprocess.run(["git", "add", "scanner.py"], check=True)
        subprocess.run(["git", "commit", "-m", "V42: Hard-Lock AI & Crypto Scanner Fix"], check=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], check=True)
        print("🔥 ERFOLG! Krypto-Scan und KI-Maulkorb sind live.")
    except: traceback.print_exc()
    input("FERTIG: ENTER...")

if __name__ == "__main__": main()