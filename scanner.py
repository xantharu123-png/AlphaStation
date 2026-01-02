<<<<<<< HEAD
import streamlit as st
import pandas as pd
import requests
import anthropic
from datetime import datetime

# =============================================================================
# 1. INITIALISIERUNG
# =============================================================================
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = "BTC"
if "scan_results" not in st.session_state:
    st.session_state.scan_results = []
if "active_filters" not in st.session_state:
    st.session_state.active_filters = {}
if "additional_filters" not in st.session_state:
    st.session_state.additional_filters = {}
if "current_strategy" not in st.session_state:
    st.session_state.current_strategy = None

# =============================================================================
# 2. STRATEGIE-DEFINITIONEN (Kritisch geprüft)
# =============================================================================
STRATEGIES = {
    "Volume Surge": {
        "description": "Aktien/Krypto mit überdurchschnittlichem Volumen",
        "filters": {
            "RVOL": (2.0, 50.0),
        },
        "logic": "RVOL > 2.0 zeigt erhöhtes Interesse"
    },
    "Bull Flag": {
        "description": "Konsolidierung nach starkem Anstieg - Volumen nimmt ab",
        "filters": {
            "Vortag %": (4.0, 25.0),
            "Change %": (-2.0, 2.0),
            "RVOL": (0.3, 1.5),
        },
        "logic": "Vortag stark positiv, heute seitwärts, Volumen sinkt = Bullflag"
    },
    "Bear Flag": {
        "description": "Konsolidierung nach Abverkauf - Short-Setup",
        "filters": {
            "Vortag %": (-25.0, -4.0),
            "Change %": (-2.0, 2.0),
            "RVOL": (0.3, 1.5),
        },
        "logic": "Vortag stark negativ, heute seitwärts, Volumen sinkt = Bearflag"
    },
    "Breakout Long": {
        "description": "Momentum-Ausbruch mit Volumen-Bestätigung",
        "filters": {
            "Change %": (5.0, 50.0),
            "RVOL": (2.0, 50.0),
            "Close Position": (0.75, 1.0),
        },
        "logic": "Starker Anstieg + hohes Volumen + Close nahe High"
    },
    "Breakdown Short": {
        "description": "Abverkauf mit Volumen - Short-Chance",
        "filters": {
            "Change %": (-50.0, -5.0),
            "RVOL": (2.0, 50.0),
            "Close Position": (0.0, 0.25),
        },
        "logic": "Starker Abverkauf + hohes Volumen + Close nahe Low"
    },
    "Penny Rockets": {
        "description": "Günstige Aktien mit explosivem Volumen",
        "filters": {
            "Preis": (0.10, 5.0),
            "RVOL": (3.0, 100.0),
            "Change %": (2.0, 100.0),
        },
        "logic": "Lowcaps unter $5 mit extremem Interesse"
    },
    "Dip Buy": {
        "description": "Qualitätsaktien im Rücksetzer ohne Panik",
        "filters": {
            "Preis": (10.0, 10000.0),
            "Change %": (-8.0, -2.0),
            "RVOL": (0.5, 2.0),
        },
        "logic": "Moderater Rücksetzer ohne Volumen-Panik = Kaufchance"
    },
    "Reversal Hunter": {
        "description": "Trendumkehr nach starkem Abverkauf",
        "filters": {
            "Vortag %": (-50.0, -5.0),
            "Change %": (2.0, 30.0),
            "RVOL": (1.5, 50.0),
        },
        "logic": "Gestern Crash, heute Käufer = potenzielle Umkehr"
    },
    "Early Momentum": {
        "description": "Starker Tagesstart mit Volumen",
        "filters": {
            "Change %": (3.0, 30.0),
            "RVOL": (1.5, 50.0),
        },
        "logic": "Positive Bewegung mit überdurchschnittlichem Volumen"
    },
    "Whale Watch": {
        "description": "Extremes Volumen - Big Player aktiv",
        "filters": {
            "RVOL": (5.0, 100.0),
        },
        "logic": "RVOL > 5.0 = institutionelles Interesse wahrscheinlich"
    },
}

# =============================================================================
# 3. HELPER FUNCTIONS
# =============================================================================
def apply_strategy(strategy_name):
    """Lädt die Basis-Filter einer Strategie"""
    if strategy_name in STRATEGIES:
        st.session_state.active_filters = STRATEGIES[strategy_name]["filters"].copy()
        st.session_state.current_strategy = strategy_name
        st.session_state.additional_filters = {
            "preis_min": 0.0,
            "preis_max": 100000.0,
            "nur_gewinner": False,
            "nur_verlierer": False,
            "rvol_override_min": None,
            "rvol_override_max": None,
        }

def calculate_close_position(high, low, close):
    """Berechnet wo der Close innerhalb der Tagesrange liegt (0=Low, 1=High)"""
    if high == low:
        return 0.5
    return (close - low) / (high - low)

def calculate_alpha_score(rvol, vortag_pct, change_pct):
    """Alpha = (RVOL * 12) + (|Vortag%| * 10) + (|Change%| * 8)"""
    return round((rvol * 12) + (abs(vortag_pct) * 10) + (abs(change_pct) * 8), 2)

# =============================================================================
# 4. STREAMLIT UI
# =============================================================================
st.set_page_config(page_title="Alpha V47 Pro", layout="wide")

# -----------------------------------------------------------------------------
# SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("💎 Alpha V47 Pro")
    st.caption("10 Strategien | Zusatzfilter | Claude AI")
    
    st.divider()
    
    # Markt-Auswahl
    m_type = st.radio("📊 Markt:", ["Krypto", "Aktien"], horizontal=True)
    
    st.divider()
    
    # Strategie-Auswahl
    st.subheader("🎯 Strategie")
    strat = st.selectbox(
        "Wähle Strategie:",
        list(STRATEGIES.keys()),
        help="Jede Strategie hat vordefinierte Filter"
    )
    
    # Strategie-Info anzeigen
    with st.expander("ℹ️ Strategie-Info"):
        st.write(f"**{strat}**")
        st.write(STRATEGIES[strat]["description"])
        st.caption(f"Logik: {STRATEGIES[strat]['logic']}")
    
    if st.button("📥 Strategie laden", use_container_width=True):
        apply_strategy(strat)
        st.rerun()
    
    st.divider()
    
    # Aktive Filter anzeigen und anpassen
    if st.session_state.active_filters:
        st.subheader("⚙️ Basis-Filter")
        st.caption(f"Strategie: {st.session_state.current_strategy}")
        
        for filter_name, values in list(st.session_state.active_filters.items()):
            if filter_name == "Close Position":
                st.session_state.active_filters[filter_name] = st.slider(
                    f"{filter_name} (0=Low, 1=High)",
                    0.0, 1.0,
                    (float(values[0]), float(values[1])),
                    step=0.05,
                    key=f"base_{filter_name}"
                )
            elif filter_name == "Preis":
                st.session_state.active_filters[filter_name] = st.slider(
                    f"{filter_name} ($)",
                    0.0, 10000.0,
                    (float(values[0]), float(values[1])),
                    key=f"base_{filter_name}"
                )
            else:
                min_val = -100.0 if "%" in filter_name else 0.0
                max_val = 100.0 if "%" in filter_name else 100.0
                st.session_state.active_filters[filter_name] = st.slider(
                    f"{filter_name}",
                    min_val, max_val,
                    (float(values[0]), float(values[1])),
                    key=f"base_{filter_name}"
                )
        
        st.divider()
        
        # Zusatzfilter
        st.subheader("🔧 Zusatzfilter")
        
        col1, col2 = st.columns(2)
        with col1:
            preis_min = st.number_input("Preis Min ($)", 0.0, 100000.0, 0.0, key="add_preis_min")
        with col2:
            preis_max = st.number_input("Preis Max ($)", 0.0, 100000.0, 100000.0, key="add_preis_max")
        
        col3, col4 = st.columns(2)
        with col3:
            nur_gewinner = st.checkbox("✅ Nur Gewinner", key="add_gewinner")
        with col4:
            nur_verlierer = st.checkbox("🔻 Nur Verlierer", key="add_verlierer")
        
        with st.expander("RVOL Override"):
            rvol_override = st.checkbox("RVOL manuell überschreiben")
            if rvol_override:
                rvol_min_override = st.number_input("RVOL Min", 0.1, 100.0, 1.0)
                rvol_max_override = st.number_input("RVOL Max", 0.1, 100.0, 50.0)
            else:
                rvol_min_override = None
                rvol_max_override = None
        
        # Speichere Zusatzfilter
        st.session_state.additional_filters = {
            "preis_min": preis_min,
            "preis_max": preis_max,
            "nur_gewinner": nur_gewinner,
            "nur_verlierer": nur_verlierer,
            "rvol_override_min": rvol_min_override if rvol_override else None,
            "rvol_override_max": rvol_max_override if rvol_override else None,
        }
    
    st.divider()
    
    # SCAN Button
    if st.button("🚀 SCAN STARTEN", type="primary", use_container_width=True):
        if not st.session_state.active_filters:
            st.warning("Bitte zuerst eine Strategie laden!")
        else:
            with st.status("Hole Live-Daten von Polygon.io...") as status:
                poly_key = st.secrets["POLYGON_KEY"]
                
                if m_type == "Krypto":
                    url = f"https://api.polygon.io/v2/snapshot/locale/global/markets/crypto/tickers?apiKey={poly_key}"
                else:
                    url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers?apiKey={poly_key}"
                
                try:
                    resp = requests.get(url, timeout=30).json()
                    tickers = resp.get("tickers", [])
                    status.update(label=f"Verarbeite {len(tickers)} Ticker...")
                    
                    # Debug: Zeige API Status
                    api_status = resp.get("status", "unknown")
                    if len(tickers) == 0:
                        st.warning(f"API Status: {api_status} - Keine Ticker erhalten. Möglicherweise API-Limit oder Markt geschlossen.")
                    
                    results = []
                    skipped_no_price = 0
                    skipped_filter = 0
                    f = st.session_state.active_filters
                    af = st.session_state.additional_filters
                    
                    for t in tickers:
                        try:
                            day = t.get("day", {}) or {}
                            prev = t.get("prevDay", {}) or {}
                            last = t.get("lastTrade", {}) or {}
                            minute_data = t.get("min", {}) or {}
                            
                            # Preis ermitteln (Fallback-Kette für Krypto)
                            price = (
                                day.get("c") or 
                                last.get("p") or 
                                minute_data.get("c") or 
                                prev.get("c") or 
                                t.get("lastQuote", {}).get("p") or
                                0
                            )
                            if price <= 0:
                                skipped_no_price += 1
                                continue
                            
                            # Metriken berechnen
                            high = day.get("h") or price
                            low = day.get("l") or price
                            close = day.get("c") or price
                            
                            # Change % - Polygon liefert das bei Krypto oft nicht direkt
                            change = t.get("todaysChangePerc")
                            if change is None:
                                # Manuell berechnen: (close - prev_close) / prev_close * 100
                                prev_close_price = prev.get("c") or day.get("o") or price
                                if prev_close_price > 0:
                                    change = ((price - prev_close_price) / prev_close_price) * 100
                                else:
                                    change = 0
                            change = change or 0
                            
                            # Volumen & RVOL
                            vol = day.get("v") or minute_data.get("v") or 0
                            prev_vol = prev.get("v") or 0
                            
                            # RVOL Berechnung mit Sicherheit
                            if prev_vol > 0 and vol > 0:
                                rvol = round(vol / prev_vol, 2)
                            else:
                                # Fallback: Wenn kein prev_vol, setze RVOL auf 1.0 (neutral)
                                rvol = 1.0
                            
                            # Cap RVOL bei extremen Werten
                            rvol = min(rvol, 999.0)
                            
                            # Vortag Change berechnen
                            prev_open = prev.get("o") or 0
                            prev_close = prev.get("c") or 0
                            if prev_open > 0:
                                vortag_chg = round(((prev_close - prev_open) / prev_open) * 100, 2)
                            else:
                                vortag_chg = 0
                            
                            # Close Position (0 = Low, 1 = High)
                            close_pos = calculate_close_position(high, low, close)
                            
                            # =================================================
                            # FILTER-LOGIK
                            # =================================================
                            match = True
                            
                            # Basis-Filter aus Strategie
                            if "RVOL" in f:
                                rvol_min, rvol_max = f["RVOL"]
                                # Override falls aktiviert
                                if af.get("rvol_override_min") is not None:
                                    rvol_min = af["rvol_override_min"]
                                if af.get("rvol_override_max") is not None:
                                    rvol_max = af["rvol_override_max"]
                                if not (rvol_min <= rvol <= rvol_max):
                                    match = False
                            
                            if "Change %" in f and not (f["Change %"][0] <= change <= f["Change %"][1]):
                                match = False
                            
                            if "Vortag %" in f and not (f["Vortag %"][0] <= vortag_chg <= f["Vortag %"][1]):
                                match = False
                            
                            if "Preis" in f and not (f["Preis"][0] <= price <= f["Preis"][1]):
                                match = False
                            
                            if "Close Position" in f and not (f["Close Position"][0] <= close_pos <= f["Close Position"][1]):
                                match = False
                            
                            # Zusatzfilter
                            if af.get("preis_min", 0) > 0 and price < af["preis_min"]:
                                match = False
                            if af.get("preis_max", 100000) < 100000 and price > af["preis_max"]:
                                match = False
                            if af.get("nur_gewinner") and change <= 0:
                                match = False
                            if af.get("nur_verlierer") and change >= 0:
                                match = False
                            
                            if not match:
                                skipped_filter += 1
                            
                            # =================================================
                            # ERGEBNIS SPEICHERN
                            # =================================================
                            if match:
                                ticker_raw = t.get("ticker", "")
                                # Krypto: X:BTCUSD -> BTC
                                ticker_clean = ticker_raw.replace("X:", "").replace("USD", "")
                                
                                alpha = calculate_alpha_score(rvol, vortag_chg, change)
                                
                                results.append({
                                    "Ticker": ticker_clean,
                                    "Preis": round(price, 4),
                                    "Chg%": round(change, 2),
                                    "RVOL": rvol,
                                    "Vortag%": vortag_chg,
                                    "ClosePos": round(close_pos, 2),
                                    "Alpha": alpha,
                                })
                        except Exception:
                            continue
                    
                    # Nach Alpha-Score sortieren
                    st.session_state.scan_results = sorted(results, key=lambda x: x["Alpha"], reverse=True)[:50]
                    
                    # Debug-Info
                    debug_msg = f"✅ {len(st.session_state.scan_results)} Signale gefunden"
                    if skipped_no_price > 0 or skipped_filter > 0:
                        debug_msg += f" | ⚠️ {skipped_no_price} ohne Preis, {skipped_filter} gefiltert"
                    status.update(label=debug_msg, state="complete")
                    
                except Exception as e:
                    st.error(f"API Fehler: {e}")

# -----------------------------------------------------------------------------
# HAUPTBEREICH
# -----------------------------------------------------------------------------
col_chart, col_journal = st.columns([2, 1])

with col_journal:
    st.subheader("📋 Scan-Ergebnisse")
    if st.session_state.current_strategy:
        st.caption(f"Strategie: {st.session_state.current_strategy}")
    
    if st.session_state.scan_results:
        df = pd.DataFrame(st.session_state.scan_results)
        
        # Farbige Darstellung
        sel = st.dataframe(
            df,
            on_select="rerun",
            selection_mode="single-row",
            hide_index=True,
            use_container_width=True,
            column_config={
                "Preis": st.column_config.NumberColumn("Preis", format="$%.4f"),
                "Chg%": st.column_config.NumberColumn("Chg%", format="%.2f%%"),
                "RVOL": st.column_config.NumberColumn("RVOL", format="%.2fx"),
                "Vortag%": st.column_config.NumberColumn("Vortag%", format="%.2f%%"),
                "ClosePos": st.column_config.ProgressColumn("ClosePos", min_value=0, max_value=1),
                "Alpha": st.column_config.NumberColumn("Alpha", format="%.1f ⭐"),
            }
        )
        
        if sel.selection and sel.selection.rows:
            row = df.iloc[sel.selection.rows[0]]
            st.session_state.selected_symbol = str(row["Ticker"])
            st.session_state.current_data = row.to_dict()
    else:
        st.info("Klicke auf 'SCAN STARTEN' um Signale zu finden")

with col_chart:
    st.subheader(f"📊 {st.session_state.selected_symbol}")
    
    # TradingView Chart
    if m_type == "Krypto":
        tv_symbol = f"BINANCE:{st.session_state.selected_symbol}USDT"
    else:
        tv_symbol = st.session_state.selected_symbol
    
    tv_html = f'''
    <div style="height:500px; border-radius: 8px; overflow: hidden;">
        <div id="tv_chart" style="height:100%"></div>
        <script src="https://s3.tradingview.com/tv.js"></script>
        <script>
            new TradingView.widget({{
                "autosize": true,
                "symbol": "{tv_symbol}",
                "interval": "240",
                "timezone": "Europe/Berlin",
                "theme": "dark",
                "style": "1",
                "locale": "de_DE",
                "toolbar_bg": "#1a1a2e",
                "enable_publishing": false,
                "hide_side_toolbar": false,
                "allow_symbol_change": true,
                "studies": ["Volume@tv-basicstudies"],
                "container_id": "tv_chart"
            }});
        </script>
    </div>
    '''
    st.components.v1.html(tv_html, height=500)

# -----------------------------------------------------------------------------
# CLAUDE AI ANALYSE
# -----------------------------------------------------------------------------
st.divider()

col_ai1, col_ai2 = st.columns([3, 1])
with col_ai1:
    st.subheader("🤖 Claude AI Analyse")
with col_ai2:
    analyze_btn = st.button("Analyse starten", type="primary", use_container_width=True)

if analyze_btn:
    if "current_data" not in st.session_state:
        st.warning("Wähle zuerst einen Ticker aus der Liste!")
    else:
        with st.spinner("Claude analysiert..."):
            try:
                d = st.session_state.current_data
                
                # News holen
                poly_key = st.secrets["POLYGON_KEY"]
                ticker_for_news = st.session_state.selected_symbol
                if m_type == "Krypto":
                    ticker_for_news = f"X:{st.session_state.selected_symbol}USD"
                
                news_resp = requests.get(
                    f"https://api.polygon.io/v2/reference/news?ticker={ticker_for_news}&limit=3&apiKey={poly_key}",
                    timeout=10
                ).json()
                news_items = news_resp.get("results", [])
                news_txt = "\n".join([f"- {n.get('title', 'N/A')}" for n in news_items]) if news_items else "Keine aktuellen News."
                
                # Claude API Call
                client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                
                prompt = f"""TECHNISCHES TERMINAL - ANALYSE

TICKER: {d['Ticker']}
STRATEGIE: {st.session_state.current_strategy}
MARKT: {m_type}

DATEN:
- Preis: ${d['Preis']}
- Tagesänderung: {d['Chg%']}%
- RVOL: {d['RVOL']}x
- Vortag-Performance: {d['Vortag%']}%
- Close Position: {d['ClosePos']} (0=Tagestief, 1=Tageshoch)
- Alpha-Score: {d['Alpha']}

NEWS:
{news_txt}

AUFGABEN:
1. Bewerte das Setup im Kontext der Strategie "{st.session_state.current_strategy}"
2. Nenne 3 konkrete Support-Level (basierend auf Preis und runden Zahlen)
3. Nenne 3 konkrete Resistance-Level
4. Berechne Risk/Reward Ratio für einen Trade
5. Gib ein Rating 1-100 für die Trade-Qualität
6. Klare Empfehlung: LONG / SHORT / ABWARTEN

Keine Disclaimers. Nur Fakten und Zahlen."""

                message = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1500,
                    system="Du bist ein präzises Finanz-Terminal für professionelle Trader. Antworte direkt, technisch und ohne Höflichkeitsfloskeln. Die gelieferten Daten sind Fakten - keine Ausreden.",
                    messages=[{"role": "user", "content": prompt}]
                )
                
                # Ausgabe
                st.markdown(f"### 📊 Report: {d['Ticker']}")
                st.caption(f"Strategie: {st.session_state.current_strategy} | Alpha: {d['Alpha']}")
                st.divider()
                st.write(message.content[0].text)
                
            except Exception as e:
                st.error(f"Fehler: {e}")

# -----------------------------------------------------------------------------
# FOOTER
# -----------------------------------------------------------------------------
st.divider()
st.caption("Alpha Station V47 Pro | 10 Strategien | Claude AI | Made for Miroslav")
=======
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
>>>>>>> d3bc7e8 (V46: Cloud-Ready)
