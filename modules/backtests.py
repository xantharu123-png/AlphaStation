"""
Backtest Module — Extrahiert aus scanner.py (V70.0)

Backtesting-Engine für verschiedene Strategien:
- BI V2 Backtest, BioTech Backtest
- Full Backtest (grouped/single)
- Trade Simulation + Statistiken
"""
import time
import datetime as dt
from datetime import datetime, timedelta
from modules.data_fetchers import rate_limited_get, fetch_grouped_daily
from modules.scorers import calculate_setup_score
from modules.strategies import BACKTEST_STRATEGY_RULES
from modules.analysis import compute_daily_metrics
from modules.helpers import check_signal
from modules.patterns import analyze_breakout_imminent
from modules.scanners import _compute_biotech_technical_from_bars
from modules.data_fetchers import fetch_backtest_daily_data
from modules.trade_levels import trade_geometry


# ── Backtest Universes (kopiert aus scanner.py) ──
BACKTEST_UNIVERSE = [
    # === Tech Large Cap (20) ===
    "AAPL", "MSFT", "NVDA", "TSLA", "META", "AMZN", "GOOG", "AMD", "INTC", "CRM",
    "AVGO", "ORCL", "ADBE", "CSCO", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC",
    # === Tech Mid/Growth (20) ===
    "PLTR", "SOFI", "SQ", "SNAP", "ROKU", "NET", "SHOP", "COIN", "CRWD", "DDOG",
    "ZS", "SNOW", "ABNB", "UBER", "LYFT", "DASH", "PINS", "U", "RBLX", "HOOD",
    # === Finance (15) ===
    "JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW", "BLK", "AXP", "V",
    "MA", "PYPL", "FIS", "ICE", "CME",
    # === Healthcare (15) ===
    "MRNA", "PFE", "ABBV", "JNJ", "UNH", "LLY", "TMO", "ABT", "BMY", "GILD",
    "AMGN", "REGN", "VRTX", "ISRG", "BIIB",
    # === Energy (10) ===
    "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HAL",
    # === Consumer (15) ===
    "WMT", "NKE", "COST", "TGT", "HD", "LOW", "SBUX", "MCD", "CMG", "DPZ",
    "LULU", "DECK", "ROST", "TJX", "DG",
    # === Industrial (10) ===
    "BA", "CAT", "DE", "GE", "HON", "UPS", "FDX", "LMT", "RTX", "NOC",
    # === Volatile/Small Cap (25) ===
    "GME", "AMC", "MARA", "RIOT", "SMCI", "ARM", "IONQ", "RGTI", "RIVN", "LCID",
    "PLUG", "FCEL", "SPCE", "OPEN", "WISH", "CLOV", "BB", "NOK", "TLRY", "SNDL",
    "MSTR", "UPST", "AFRM", "PATH", "AI",
    # === Biotech/Pharma (15) ===
    "NVAX", "BNTX", "DNA", "CRSP", "BEAM", "EDIT", "NTLA", "FATE", "SGEN", "ARKG",
    "EXAS", "HIMS", "DOCS", "ACHR", "JOBY",
    # === Semiconductor (10) ===
    "MRVL", "ON", "SWKS", "QRVO", "WOLF", "SMTC", "CRUS", "ALGM", "POWI", "DIOD",
    # === Real Estate / REITs (10) ===
    "O", "AMT", "PLD", "SPG", "VICI", "MPW", "IRM", "DLR", "CCI", "EQIX",
    # === ETFs (8) ===
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "ARKK"
]

BIOTECH_BACKTEST_UNIVERSE = [
    # Large Cap Biotech (>$20B) — kleinere Moves aber liquide
    "AMGN", "GILD", "REGN", "VRTX", "BIIB", "MRNA", "ALNY", "BMRN",
    # Mid Cap Biotech ($2-20B) — Sweet Spot für Catalyst-Trading
    "SRPT", "EXEL", "PCVX", "IONS", "NBIX", "HALO", "INSM", "CRNX",
    "RARE", "MYGN", "FOLD", "ARWR", "IOVA", "KRYS", "ITCI", "CORT",
    "DAWN", "RVMD", "SWTX", "VKTX", "CYTK", "TGTX", "AXSM", "ADMA",
    # Small Cap Biotech ($200M-2B) — hohes Catalyst-Upside
    "AGEN", "ALVR", "ARQT", "AVXL", "BCRX", "CARA", "CLDX", "CPRX",
    "DVAX", "ENTA", "GERN", "HRTX", "IMVT", "KALA", "LQDA", "MNKD",
    "NUVB", "OCUL", "PLRX", "PRAX", "RCKT", "SAGE", "SAVA", "SMMT",
    "TVTX", "VCEL", "VRNA", "XNCR", "ZYME", "APLS", "ACLX", "CDTX",
    # Recent FDA-Active (regelmäßig PDUFA/AdCom Dates)
    "ACAD", "AKBA", "ANIK", "BLUE", "BLTE", "CMPS", "CRSP", "EDIT",
    "NTLA", "BEAM", "MDGL", "KROS", "LNTH", "LGND", "NKTR", "PCRX",
    "PTCT", "RLAY", "ROIV", "RYTM", "SNDX", "TARS", "TECH", "TXRX",
]



def run_full_backtest_grouped(poly_key, strategies=None, months=6, min_price=5.0, 
                               min_volume=100000, progress_callback=None):
    """
    Backtest über ALLE US-Aktien mit Grouped Daily Bars.
    
    Zwei-Pass Ansatz:
    1. Lade alle Tage → baue per-Ticker History auf
    2. Scanne Signale und simuliere Trades mit vollständiger History
    """
    if strategies is None:
        strategies = list(BACKTEST_STRATEGY_RULES.keys())
    
    end_dt = datetime.now() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=months * 30 + 30)  # +30 für RVOL Lookback
    test_start = (end_dt - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    
    # Generiere Handelstage (Mo-Fr)
    trading_days = []
    current = start_dt
    while current <= end_dt:
        if current.weekday() < 5:
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    if not trading_days:
        return {s: [] for s in strategies}, 0
    
    # ============================================================
    # PASS 1: Lade alle Tage und baue per-Ticker History auf
    # ============================================================
    ticker_history = {}  # ticker → list of bars (chronologisch)
    total_tickers_seen = set()
    
    for day_idx, date_str in enumerate(trading_days):
        if progress_callback:
            progress_callback(
                (day_idx / len(trading_days)) * 0.7,  # 70% für Laden
                f" Lade Tag {day_idx+1}/{len(trading_days)}: {date_str}"
            )
        
        day_data = fetch_grouped_daily(poly_key, date_str)
        if not day_data:
            continue
        
        for ticker, r in day_data.items():
            if len(ticker) > 5 or "." in ticker:
                continue
            
            # Leveraged/Inverse ETFs und Krypto-ETPs rausfiltern
            _t = ticker.upper()
            _skip_prefixes = (
                "TQQQ","SQQQ","SOXL","SOXS","LABU","LABD","SPXL","SPXS",
                "UPRO","SPXU","UVXY","SVXY","NUGT","DUST","JNUG","JDST",
                "FNGU","FNGD","TECL","TECS","BULZ","BERZ","GUSH","DRIP",
                "FAS","FAZ","UDOW","SDOW","YANG","YINN","ERX","ERY",
                "XRPT","XXRP","XETH","BITO","GBTC","ETHE","BITW","CONL",
                "MSOX","BTFX","SOLT","NEBX","AREC","MAXI","TNA","TZA"
            )
            if any(_t.startswith(p) for p in _skip_prefixes):
                continue
            
            price = r.get("c", 0)
            volume = r.get("v", 0)
            
            if price <= 0:
                continue

            # Preserve the complete path. Filtering history here can hide
            # later crash bars and stop hits; eligibility is checked only on
            # the signal bar below.
            if price >= min_price and volume >= min_volume:
                total_tickers_seen.add(ticker)
            
            bar = {
                "date": date_str,
                "open": r.get("o", 0),
                "high": r.get("h", 0),
                "low": r.get("l", 0),
                "close": price,
                "volume": volume,
            }
            
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append(bar)
    
    # ============================================================
    # PASS 2: Signale erkennen + Trades simulieren
    # (Jetzt hat jeder Ticker seine VOLLSTÄNDIGE History)
    # ============================================================
    all_results = {s: [] for s in strategies}
    tickers_with_data = [t for t, bars in ticker_history.items() if len(bars) >= 30]
    seen_signals = set()  # Dedup: max 1 Signal pro Ticker pro Tag
    
    for t_idx, ticker in enumerate(tickers_with_data):
        if progress_callback and t_idx % 500 == 0:
            progress_callback(
                0.7 + (t_idx / len(tickers_with_data)) * 0.3,  # 30% für Simulation
                f" Scanne {ticker} ({t_idx+1}/{len(tickers_with_data)})"
            )
        
        bars = ticker_history[ticker]
        
        for idx in range(21, len(bars)):
            if bars[idx]["date"] < test_start:
                continue
            
            metrics = compute_daily_metrics(bars, idx)
            if not metrics or metrics["price"] <= 0:
                continue
            if metrics["price"] < min_price or float(bars[idx].get("volume") or 0) < min_volume:
                continue
            
            for strat_name in strategies:
                strat = BACKTEST_STRATEGY_RULES[strat_name]
                if metrics["price"] < strat.get("min_price", 1.0):
                    continue
                
                if check_signal(metrics, strat["signal"]):
                    # Dedup: Max 1 Trade pro Ticker pro Tag
                    dedup_key = (ticker, bars[idx]["date"], strat_name)
                    if dedup_key in seen_signals:
                        continue
                    
                    trade = simulate_trade(bars, idx, strat)
                    if trade:
                        seen_signals.add(dedup_key)
                        trade["ticker"] = ticker
                        trade["strategy"] = strat_name
                        trade["signal_change_pct"] = round(metrics["change_pct"], 2)
                        trade["signal_rvol"] = round(metrics["rvol"], 1)
                        all_results[strat_name].append(trade)
    
    # Memory aufräumen
    del ticker_history
    
    if progress_callback:
        progress_callback(1.0, f"[OK] Fertig! {len(total_tickers_seen)} Aktien gescannt")

    return all_results, len(total_tickers_seen)


def run_bi_v2_backtest(poly_key, direction="long", months=6, max_tickers=200,
                        min_price=5.0, min_volume=200000, progress_callback=None):
    """
     Breakout Imminent V2.1 Backtest — Rolling-Window Analyse (Pro-Reweighted).

    V2.1 Upgrades:
    - Signal-Gewichte rebalanciert: Smart Money BOOSTED, Dead Stock CUT
    - Minervini Volume Confirmation: Breakout-Volume >= 1.4x Avg (40% über Normal)
    - Löst Grade-B-Inversion (tote Aktien kommen nicht mehr auf hohe Scores)

    Für jeden Tag im Backtest-Zeitraum:
    1. Nimm 50-Tage Fenster als Input für analyze_breakout_imminent()
    2. Bei gültigem Signal + Volume Confirmation → Breakout-Retest Entry
    3. Simuliere Trade mit 3-Phase System (Breakout → Retest → Management)
    4. Tracke Ergebnis nach Grade (S/A/B/C)

    Args:
        poly_key: Polygon API Key
        direction: "long" oder "short"
        months: Backtest-Zeitraum in Monaten
        max_tickers: Maximal analysierte Ticker (Performance-Limit)
        min_price: Mindestpreis Filter
        min_volume: Mindestvolumen/Tag Filter
        progress_callback: (pct, text) Callback für UI

    Returns:
        dict mit trades, stats_by_grade, summary
    """
    end_dt = datetime.now() - timedelta(days=1)
    window_size = 50  # 50 Bars für MACD (braucht 35+) und bessere Pattern-Erkennung
    start_dt = end_dt - timedelta(days=months * 30 + window_size + 20)  # +window+buffer
    test_start = (end_dt - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    # Generiere Handelstage (Mo-Fr)
    trading_days = []
    current = start_dt
    while current <= end_dt:
        if current.weekday() < 5:
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    if not trading_days:
        return {"trades": [], "stats_by_grade": {}, "summary": {}, "n_tickers": 0}

    # ============================================================
    # PASS 1: Lade alle Tage und baue per-Ticker History auf
    # ============================================================
    ticker_history = {}
    total_tickers_seen = set()

    for day_idx, date_str in enumerate(trading_days):
        if progress_callback:
            progress_callback(
                (day_idx / len(trading_days)) * 0.5,
                f" Lade Tag {day_idx+1}/{len(trading_days)}: {date_str}"
            )

        day_data = fetch_grouped_daily(poly_key, date_str)
        if not day_data:
            continue

        for ticker, r in day_data.items():
            if len(ticker) > 5 or "." in ticker:
                continue

            _t = ticker.upper()
            _skip_prefixes = (
                "TQQQ","SQQQ","SOXL","SOXS","LABU","LABD","SPXL","SPXS",
                "UPRO","SPXU","UVXY","SVXY","NUGT","DUST","JNUG","JDST",
                "FNGU","FNGD","TECL","TECS","BULZ","BERZ","GUSH","DRIP",
                "FAS","FAZ","UDOW","SDOW","YANG","YINN","ERX","ERY",
                "XRPT","XXRP","XETH","BITO","GBTC","ETHE","BITW","CONL",
                "MSOX","BTFX","SOLT","NEBX","AREC","MAXI","TNA","TZA"
            )
            if any(_t.startswith(p) for p in _skip_prefixes):
                continue

            price = r.get("c", 0)
            volume = r.get("v", 0)
            if price <= 0:
                continue
            if price >= min_price and volume >= min_volume:
                total_tickers_seen.add(ticker)
            bar = {
                "date": date_str,
                "open": r.get("o", 0),
                "high": r.get("h", 0),
                "low": r.get("l", 0),
                "close": price,
                "volume": volume,
                "time": date_str,
            }
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append(bar)

    # Sortiere und filtere Ticker — Mid-Caps (500K-10M Vol) sind BI-Goldzone
    ticker_avg_vol = {}
    for t, bars_list in ticker_history.items():
        if len(bars_list) >= (window_size + 5):  # Genug History für Window + Simulation
            avg_vol = sum(b["volume"] for b in bars_list[-20:]) / 20
            ticker_avg_vol[t] = avg_vol

    # Priorisiere Mid-Cap-Volumen (500K-10M) — hier passieren die besten Breakouts
    # Aber schliesse High-Volume nicht komplett aus (niedrigere Prio)
    midcap_tickers = {t: v for t, v in ticker_avg_vol.items() if 500_000 <= v <= 10_000_000}
    largecap_tickers = {t: v for t, v in ticker_avg_vol.items() if v > 10_000_000}

    # Mid-Caps zuerst (sortiert nach Vol), dann Large-Caps auffüllen
    sorted_midcap = sorted(midcap_tickers.keys(), key=lambda t: midcap_tickers[t], reverse=True)
    sorted_largecap = sorted(largecap_tickers.keys(), key=lambda t: largecap_tickers[t], reverse=True)
    tickers_to_test = (sorted_midcap + sorted_largecap)[:max_tickers]

    # ============================================================
    # PASS 2: Rolling-Window Breakout Imminent Analyse + Trade Sim
    # ============================================================
    all_trades = []
    signals_found = 0
    cooldown = {}  # ticker → last signal date (vermeidet Doppel-Signale)

    for t_idx, ticker in enumerate(tickers_to_test):
        if progress_callback and t_idx % 20 == 0:
            progress_callback(
                0.5 + (t_idx / len(tickers_to_test)) * 0.5,
                f" Analysiere {ticker} ({t_idx+1}/{len(tickers_to_test)}) | {signals_found} Signale"
            )

        bars = ticker_history[ticker]

        # Für jeden Tag ab test_start: 50-Bar Fenster → BI V2 Analyse
        for idx in range(window_size, len(bars)):
            if bars[idx]["date"] < test_start:
                continue

            # Cooldown: Min 7 Tage zwischen Signalen pro Ticker
            if ticker in cooldown:
                last_sig_idx = cooldown[ticker]
                if idx - last_sig_idx < 7:
                    continue

            # 50-Bar Rolling Window (genug für MACD 26+9=35)
            window = bars[idx-window_size:idx]
            if (
                float(window[-1].get("close") or 0) < min_price
                or float(window[-1].get("volume") or 0) < min_volume
            ):
                continue

            result = analyze_breakout_imminent(window, direction=direction)
            # V2.1+: Returns 8 values (mit smart_money_fires, smart_money_hits)
            if len(result) == 8:
                is_valid, bi_score, bi_max, details, confidence, grade, sm_fires, sm_hits = result
            else:
                is_valid, bi_score, bi_max, details, confidence, grade = result
                sm_fires, sm_hits = 0, 0

            if not is_valid:
                continue

            # Grade-Filter: C+ traden (D zu schwach)
            if grade == "D":
                continue

            # SMART MONEY MINIMUM: Min 2 Boosted-Signale müssen feuern ([*] oder [OK])
            # Das ist der WR-Booster — ohne Smart Money = kein Trade
            if sm_hits < 2:
                continue

            # Qualitäts-Filter (identisch zum Live-Scanner)
            range_high = max(b["high"] for b in window[-15:])
            range_low = min(b["low"] for b in window[-15:])
            range_size = range_high - range_low
            range_pct = (range_size / range_low * 100) if range_low > 0 else 0

            if range_pct < 2.0:
                continue

            _adr_bars = [b for b in window[-10:] if b["close"] > 0]
            avg_daily_range = sum((b["high"] - b["low"]) / b["close"] * 100 for b in _adr_bars) / len(_adr_bars) if _adr_bars else 0
            if avg_daily_range < 0.3:
                continue

            # ============================================
            # BREAKOUT-RETEST ENTRY V3.1 (Post-Audit Fix)
            # ============================================
            # Fixes: #1 Phase-2 Logik, #2 Risk-Calc, #3 Stop-Weite,
            #        #4 Same-Day Entry, #5-8 HIGH Issues
            # ============================================
            # Calculate True Range for last 5 bars
            tr_values = []
            for i, b in enumerate(window[-5:]):
                high = b["high"]
                low = b["low"]
                if i == 0:
                    # First bar: use simple range
                    tr = high - low
                else:
                    prev_close = window[-5 + i - 1]["close"]
                    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_values.append(tr)
            atr_5 = sum(tr_values) / len(tr_values)
            breakout_threshold = atr_5 * 0.25  # ATR-basiert statt fixer 0.5% (#20)

            # Stop ATR×1.2 für alle Grades (bewährt — NICHT ändern!)
            stop_atr_mult = 1.2

            # V2.8: Grade C bekommt engere Targets (realistischer für schwächere Signale)
            # Grade B+: TP1=1.0×Range, TP2=2.0×Range (Standard)
            # Grade C:  TP1=0.7×Range, TP2=1.4×Range (näher → höhere TP1 Rate → mehr Teilgewinne)
            tp1_mult = 0.7 if grade == "C" else 1.0
            tp2_mult = 1.4 if grade == "C" else 2.0

            if direction == "long":
                breakout_level = round(range_high, 4)
                retest_zone_upper = round(range_high + atr_5 * 0.15, 4)  # Knapp über Range-High
                retest_zone_lower = round(range_high - atr_5 * 0.3, 4)   # Leicht unter Range-High
                stop_price = round(range_high - atr_5 * stop_atr_mult, 4)
                tp1_price = round(range_high + range_size * tp1_mult, 4)
                tp2_price = round(range_high + range_size * tp2_mult, 4)
            else:
                breakout_level = round(range_low, 4)
                retest_zone_upper = round(range_low + atr_5 * 0.3, 4)
                retest_zone_lower = round(range_low - atr_5 * 0.15, 4)
                stop_price = round(range_low + atr_5 * stop_atr_mult, 4)
                tp1_price = round(range_low - range_size * tp1_mult, 4)
                tp2_price = round(range_low - range_size * tp2_mult, 4)

            # FIX #6: Validierung Stop auf korrekter Seite
            if direction == "long" and stop_price >= range_high:
                continue
            if direction == "short" and stop_price <= range_low:
                continue

            # Risk/Reward uses the same signed geometry as live and alert paths.
            est_entry = (retest_zone_upper + retest_zone_lower) / 2
            geometry = trade_geometry(
                est_entry,
                stop_price,
                tp1_price,
                tp2_price,
                direction.upper(),
            )
            if not geometry["valid"]:
                continue
            risk = geometry["risk"]
            rr = geometry["rr"] or 0

            if rr < 2.0:  # FIX #5: Min 2.0:1 R:R (realistischer mit weiterem Stop)
                continue

            if est_entry <= 0 or stop_price <= 0 or risk < (atr_5 * 0.25):
                continue

            signals_found += 1
            cooldown[ticker] = idx

            # === BREAKOUT-RETEST TRADE SIMULATION V3.1 ===
            max_hold = 20
            slippage = 0.001

            trade_result = {
                "ticker": ticker,
                "signal_date": bars[idx-1]["date"],
                "grade": grade,
                "score": bi_score,
                "max_score": bi_max,
                "confidence": confidence,
                "smart_money_fires": sm_fires,
                "smart_money_hits": sm_hits,
                "direction": direction.upper(),
                "entry_target": round(est_entry, 4),
                "stop_target": stop_price,
                "tp1_target": tp1_price,
                "tp2_target": tp2_price,
                "rr_planned": rr,
                "range_pct": round(range_pct, 1),
            }

            # === VOLUME AVERAGE für Breakout-Confirmation ===
            avg_vol_20 = sum(b["volume"] for b in window[-20:]) / 20 if len(window) >= 20 else sum(b["volume"] for b in window) / len(window)

            # === BREAKOUT-FILTER (V2.8 — Grade-abhängig für C) ===
            # Grade B+: Standard Minervini 1.4x Volume
            # Grade C:  Strengerer Filter 1.6x (schwächere Signale brauchen stärkere Bestätigung)
            if grade == "C":
                vol_multiplier = 1.6    # Strengere Confirmation für schwache Signale
            else:
                vol_multiplier = 1.4    # Standard Minervini für B/A/S
            min_rr = 2.0           # Standard R:R Minimum

            if rr < min_rr:
                continue

            # === TREND CONFIRMATION (Weinstein Stage 2 Filter) ===
            # 20-Tage-MA muss in Richtung des Trades zeigen
            w_closes = [b["close"] for b in window]
            ma_20_current = sum(w_closes[-20:]) / 20 if len(w_closes) >= 20 else sum(w_closes) / len(w_closes)
            ma_20_prev = sum(w_closes[-40:-20]) / 20 if len(w_closes) >= 40 else ma_20_current
            if direction == "long":
                trend_ok = ma_20_current > ma_20_prev  # MA steigt = bullischer Trend
                price_above_ma = window[-1]["close"] > ma_20_current  # Preis über MA
            else:
                trend_ok = ma_20_current < ma_20_prev  # MA fällt = bärischer Trend
                price_above_ma = window[-1]["close"] < ma_20_current  # Preis unter MA

            # V2.8: Grade C braucht BEIDE Trend-Bedingungen (strenger)
            # Grade B+: Eine reicht (wie bisher)
            if grade == "C":
                if not (trend_ok and price_above_ma):
                    continue  # Grade C: Nur MIT Trend traden
            else:
                if not (trend_ok or price_above_ma):
                    continue  # Grade B+: Eine Bedingung reicht

            # 3-Phase Simulation:
            # Phase 1: Breakout bestätigt (Close über/unter Range + ATR-Threshold + Volume)
            # Phase 2: Pullback in die Retest-Zone (zwischen upper/lower)
            # Phase 3: Trade Management (Stop/TP/Breakeven)
            entry_filled = False
            breakout_confirmed = False
            breakout_high = 0  # Höchster Punkt nach Breakout (für Pullback-Check)
            actual_entry = None
            entry_date = None
            exit_price = None
            exit_reason = None
            exit_date = None
            tp1_hit = False
            bars_held = 0
            current_stop = stop_price

            for day_offset in range(1, max_hold + 1):
                future_idx = idx + day_offset - 1
                if future_idx >= len(bars):
                    break

                future_bar = bars[future_idx]
                entered_this_bar = False

                # === PHASE 1: Breakout-Bestätigung ===
                if not breakout_confirmed:
                    # Volume Confirmation: grade-abhängig (B+: 2.0x, C: 1.4x)
                    vol_ok = future_bar["volume"] >= avg_vol_20 * vol_multiplier

                    if direction == "long" and future_bar["close"] > breakout_level + breakout_threshold and vol_ok:
                        breakout_confirmed = True
                        breakout_high = future_bar["high"]
                        # FIX #4: KEIN continue — prüfe sofort ob Entry möglich
                    elif direction == "short" and future_bar["close"] < breakout_level - breakout_threshold and vol_ok:
                        breakout_confirmed = True
                        breakout_high = future_bar["low"]  # Tiefster Punkt für Short
                    elif day_offset >= 7:
                        break
                    else:
                        continue  # Kein Breakout → nächster Tag

                # === PHASE 2: Pullback in Retest-Zone ===
                if not entry_filled:
                    if direction == "long":
                        breakout_high = max(breakout_high, future_bar["high"])
                        # FIX #1: Echter Pullback = Preis WAR höher und kommt ZURÜCK
                        price_pulled_back = future_bar["low"] <= retest_zone_upper
                        price_above_stop = future_bar["low"] > stop_price
                        had_upward_move = breakout_high > retest_zone_upper  # War schon höher

                        if price_pulled_back and price_above_stop and had_upward_move:
                            actual_entry = max(future_bar["close"], retest_zone_lower) * (1 + slippage)
                            entry_filled = True
                            entered_this_bar = True
                            entry_date = future_bar["date"]
                            bars_held = 0
                            # Recalculate signed risk with the actually filled entry.
                            risk = actual_entry - stop_price
                            if risk <= 0:
                                entry_filled = False
                                actual_entry = None
                                entry_date = None
                                break
                    else:  # short
                        breakout_high = min(breakout_high, future_bar["low"])
                        price_pulled_back = future_bar["high"] >= retest_zone_lower
                        price_below_stop = future_bar["high"] < stop_price
                        had_downward_move = breakout_high < retest_zone_lower

                        if price_pulled_back and price_below_stop and had_downward_move:
                            actual_entry = min(future_bar["close"], retest_zone_upper) * (1 - slippage)
                            entry_filled = True
                            entered_this_bar = True
                            entry_date = future_bar["date"]
                            bars_held = 0
                            risk = stop_price - actual_entry
                            if risk <= 0:
                                entry_filled = False
                                actual_entry = None
                                entry_date = None
                                break

                    if day_offset >= 15 and not entry_filled:
                        break
                    if not entry_filled:
                        continue

                # The retest fill is modelled at this daily bar's close. Its
                # earlier high/low cannot be reused as post-entry execution.
                if entered_this_bar:
                    continue

                bars_held += 1

                # Prüfe Stop und Target (Intraday via Open-Proximity + Breakeven-Stop)
                bar_open = future_bar["open"]

                if direction == "long":
                    stop_hit = future_bar["low"] <= current_stop  # OPT 4: current_stop statt stop_price
                    tp1_possible = future_bar["high"] >= tp1_price
                    tp2_possible = future_bar["high"] >= tp2_price

                    if stop_hit and tp2_possible:
                        # Daily OHLC cannot reveal whether stop or target traded
                        # first. Use the conservative stop outcome instead of an
                        # optimistic open-distance guess.
                        runner_exit = current_stop * (1 - slippage)
                        tp1_exit = tp1_price * (1 - slippage)
                        exit_price = (tp1_exit + runner_exit) / 2 if tp1_hit else runner_exit
                        exit_reason = "BE_STOP" if tp1_hit else "STOP"
                        exit_date = future_bar["date"]
                        break
                    elif stop_hit:
                        runner_exit = current_stop * (1 - slippage)
                        tp1_exit = tp1_price * (1 - slippage)
                        exit_price = (tp1_exit + runner_exit) / 2 if tp1_hit else runner_exit
                        exit_reason = "BE_STOP" if tp1_hit else "STOP"
                        exit_date = future_bar["date"]
                        break
                    else:
                        if tp1_possible and not tp1_hit:
                            tp1_hit = True
                            # Trail-Stop auf 66% zwischen Entry und TP1 (sichert mehr Gewinn)
                            trail_level = actual_entry + (tp1_price - actual_entry) * 0.66
                            current_stop = trail_level
                        if tp2_possible:
                            tp1_exit = tp1_price * (1 - slippage)
                            tp2_exit = tp2_price * (1 - slippage)
                            exit_price = (tp1_exit + tp2_exit) / 2
                            exit_reason = "TP2"
                            exit_date = future_bar["date"]
                            break
                else:  # short
                    stop_hit = future_bar["high"] >= current_stop
                    tp1_possible = future_bar["low"] <= tp1_price
                    tp2_possible = future_bar["low"] <= tp2_price

                    if stop_hit and tp2_possible:
                        # Same ambiguity for shorts: without intraday bars the
                        # only defensible fill assumption is the adverse one.
                        runner_exit = current_stop * (1 + slippage)
                        tp1_exit = tp1_price * (1 + slippage)
                        exit_price = (tp1_exit + runner_exit) / 2 if tp1_hit else runner_exit
                        exit_reason = "TRAIL_STOP" if tp1_hit else "STOP"
                        exit_date = future_bar["date"]
                        break
                    elif stop_hit:
                        runner_exit = current_stop * (1 + slippage)
                        tp1_exit = tp1_price * (1 + slippage)
                        exit_price = (tp1_exit + runner_exit) / 2 if tp1_hit else runner_exit
                        exit_reason = "TRAIL_STOP" if tp1_hit else "STOP"
                        exit_date = future_bar["date"]
                        break
                    else:
                        if tp1_possible and not tp1_hit:
                            tp1_hit = True
                            # Trail-Stop auf 66% zwischen Entry und TP1 (Short)
                            trail_level = actual_entry - (actual_entry - tp1_price) * 0.66
                            current_stop = trail_level
                        if tp2_possible:
                            tp1_exit = tp1_price * (1 + slippage)
                            tp2_exit = tp2_price * (1 + slippage)
                            exit_price = (tp1_exit + tp2_exit) / 2
                            exit_reason = "TP2"
                            exit_date = future_bar["date"]
                            break

            # Trade-Ende: OPT 5 — Partial-Exit Simulation
            # Wenn TP1 erreicht aber TP2 nicht → 50% Gewinn von TP1 + 50% am Close
            if entry_filled and exit_price is None:
                if tp1_hit:
                    # TP1 wurde erreicht, TP2 nicht → simuliere 50/50 Split
                    last_idx = min(idx + max_hold - 1, len(bars)-1)
                    tp1_exit = tp1_price * (1 - slippage if direction == "long" else 1 + slippage)
                    close_exit = bars[last_idx]["close"]
                    # Gewichteter Exit: 50% TP1 + 50% Close (BE-Stop schützt 2. Hälfte)
                    # V68: Direction-aware BE — Long: max (min Entry), Short: min (max Entry)
                    if direction == "long":
                        be_protected = max(close_exit, actual_entry)  # Close mindestens Entry
                    else:
                        be_protected = min(close_exit, actual_entry)  # Close höchstens Entry
                    exit_price = (tp1_exit + be_protected) / 2
                    exit_reason = "TP1_PARTIAL"
                    exit_date = bars[last_idx]["date"]
                elif entry_filled:
                    # Max Hold → Exit at Close, aber min Breakeven wenn im Plus
                    last_idx = min(idx + max_hold - 1, len(bars)-1)
                    close_price = bars[last_idx]["close"]
                    if direction == "long":
                        # Wenn Close unter Entry → raus bei Entry (Breakeven)
                        exit_price = max(close_price, actual_entry) if tp1_hit else close_price
                    else:
                        exit_price = min(close_price, actual_entry) if tp1_hit else close_price
                    exit_reason = "MAX_HOLD"
                    exit_date = bars[last_idx]["date"]

            if not entry_filled or actual_entry is None or exit_price is None:
                trade_result["outcome"] = "NO_FILL"
                trade_result["pnl_pct"] = 0
                trade_result["r_multiple"] = 0
                trade_result["is_winner"] = False
            else:
                if direction == "long":
                    pnl_pct = ((exit_price - actual_entry) / actual_entry) * 100 - 0.2  # 0.1% entry + 0.1% exit fee
                else:
                    pnl_pct = ((actual_entry - exit_price) / actual_entry) * 100 - 0.2  # 0.1% entry + 0.1% exit fee

                r_multiple = round(pnl_pct / (risk / actual_entry * 100), 2) if risk > 0 else 0

                trade_result["actual_entry"] = round(actual_entry, 2)
                trade_result["exit_price"] = round(exit_price, 2)
                trade_result["entry_date"] = entry_date
                trade_result["exit_date"] = exit_date
                trade_result["exit_reason"] = exit_reason
                trade_result["bars_held"] = bars_held
                trade_result["tp1_hit"] = tp1_hit
                trade_result["pnl_pct"] = round(pnl_pct, 2)
                trade_result["r_multiple"] = r_multiple
                trade_result["is_winner"] = pnl_pct > 0
                trade_result["outcome"] = exit_reason

            all_trades.append(trade_result)

    # ============================================================
    # STATISTIKEN nach Grade
    # ============================================================
    stats_by_grade = {}
    for g in ["S", "A", "B", "C", "D"]:
        grade_trades = [t for t in all_trades if t["grade"] == g and t.get("outcome") != "NO_FILL"]
        if not grade_trades:
            continue

        winners = [t for t in grade_trades if t["is_winner"]]
        losers = [t for t in grade_trades if not t["is_winner"]]

        total_pnl = sum(t["pnl_pct"] for t in grade_trades)
        avg_pnl = total_pnl / len(grade_trades) if grade_trades else 0
        avg_winner = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
        avg_loser = sum(t["pnl_pct"] for t in losers) / len(losers) if losers else 0

        win_rate = len(winners) / len(grade_trades) * 100 if grade_trades else 0

        # Profit Factor
        gross_profit = sum(t["pnl_pct"] for t in winners)
        gross_loss = abs(sum(t["pnl_pct"] for t in losers))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0

        # Avg R
        avg_r = sum(t["r_multiple"] for t in grade_trades) / len(grade_trades) if grade_trades else 0

        tp1_hits = sum(1 for t in grade_trades if t.get("tp1_hit", False))
        tp2_hits = sum(1 for t in grade_trades if t.get("outcome") == "TP2")

        stats_by_grade[g] = {
            "total": len(grade_trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(avg_pnl, 2),
            "avg_winner": round(avg_winner, 2),
            "avg_loser": round(avg_loser, 2),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": profit_factor,
            "avg_r": round(avg_r, 2),
            "tp1_rate": round(tp1_hits / len(grade_trades) * 100, 1) if grade_trades else 0,
            "tp2_rate": round(tp2_hits / len(grade_trades) * 100, 1) if grade_trades else 0,
        }

    filled_trades = [t for t in all_trades if t.get("outcome") != "NO_FILL"]

    # Calculate Max Drawdown from equity curve
    equity = 10000
    peak = equity
    max_dd = 0
    for trade in filled_trades:
        equity *= (1 + trade["pnl_pct"] / 100)
        peak = max(peak, equity)
        dd = ((peak - equity) / peak) * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    summary = {
        "total_signals": signals_found,
        "total_filled": len(filled_trades),
        "no_fill": len(all_trades) - len(filled_trades),
        "win_rate": round(sum(1 for t in filled_trades if t["is_winner"]) / len(filled_trades) * 100, 1) if filled_trades else 0,
        "avg_pnl": round(sum(t["pnl_pct"] for t in filled_trades) / len(filled_trades), 2) if filled_trades else 0,
        "total_pnl": round(sum(t["pnl_pct"] for t in filled_trades), 2) if filled_trades else 0,
        "max_drawdown": round(max_dd, 2),
        "n_tickers": len(tickers_to_test),
        "n_tickers_total": len(total_tickers_seen),
        "n_midcap": len(sorted_midcap),
        "n_largecap": len(sorted_largecap),
        "direction": direction,
        "months": months,
    }

    del ticker_history

    if progress_callback:
        progress_callback(1.0, f"[OK] BI V2 Backtest fertig! {signals_found} Signale, {len(filled_trades)} Trades")

    return {"trades": all_trades, "stats_by_grade": stats_by_grade, "summary": summary}


def run_biotech_backtest(poly_key, months=6, max_tickers=100,
                          min_price=2.0, min_volume=100000, progress_callback=None):
    """
     BioTech Catalyst Backtest — Technisches Setup + Volume Confirmation.

    KONZEPT: Biotech-Aktien werden CATALYST-GETRIEBEN getradet.
    Da historische Catalyst-Daten (FDA Dates, News) nicht verfügbar sind,
    nutzt dieser Backtest VOLUME SPIKES als Proxy für Catalyst-Aktivität:
    - Unusual Volume (RVOL >= 2.0) = Smart Money kauft vor Catalyst
    - Kombiniert mit Technical Setup Score für Qualitätsfilter
    - Biotech-spezifische Parameter: breitere Stops, größere Targets

    Entry-Logik (NICHT Breakout-Retest wie BI, sondern Momentum):
    1. Signal: RVOL >= 2.0 + Technical Score >= 10/20 + Uptrend
    2. Entry: Next Day Open (Momentum-Einstieg)
    3. Stop: 1.5 × ATR unter Entry (breiter wegen Biotech-Volatilität)
    4. TP1: 2.0R, TP2: 4.0R (Biotech-Moves sind größer als normale Aktien)
    5. Max Hold: 15 Tage (Catalyst-Trades sind kürzer)

    Architektur: 2-Pass wie BI V2 (Grouped Daily API)
    - Pass 1: Lade alle Tage, filtere auf Biotech-Ticker
    - Pass 2: Rolling-Window Analyse + Trade Simulation

    Args:
        poly_key: Polygon API Key
        months: Backtest-Zeitraum in Monaten
        max_tickers: Maximal analysierte Ticker
        min_price: Mindestpreis ($2 für Biotech — Penny Stocks inkl.)
        min_volume: Mindestvolumen/Tag
        progress_callback: (pct, text) Callback für UI

    Returns:
        dict mit trades, stats_by_grade, summary
    """
    end_dt = datetime.now() - timedelta(days=1)
    window_size = 50  # 50 Bars für technische Indikatoren (SMA50 braucht 50)
    start_dt = end_dt - timedelta(days=months * 30 + window_size + 20)
    test_start = (end_dt - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    # Biotech Universum — nutze kuratierte Liste
    biotech_set = set(t.upper() for t in BIOTECH_BACKTEST_UNIVERSE)

    # Generiere Handelstage (Mo-Fr)
    trading_days = []
    current = start_dt
    while current <= end_dt:
        if current.weekday() < 5:
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    if not trading_days:
        return {"trades": [], "stats_by_grade": {}, "summary": {}, "n_tickers": 0}

    # ============================================================
    # PASS 1: Lade alle Tage und baue per-Ticker History auf
    # ============================================================
    ticker_history = {}
    total_tickers_seen = set()

    for day_idx, date_str in enumerate(trading_days):
        if progress_callback:
            progress_callback(
                (day_idx / len(trading_days)) * 0.5,
                f" Lade Tag {day_idx+1}/{len(trading_days)}: {date_str}"
            )

        day_data = fetch_grouped_daily(poly_key, date_str)
        if not day_data:
            continue

        for ticker, r in day_data.items():
            # Nur Biotech-Ticker aus kuratieter Liste
            if ticker.upper() not in biotech_set:
                continue

            if len(ticker) > 5 or "." in ticker:
                continue

            price = r.get("c", 0)
            volume = r.get("v", 0)
            if price <= 0:
                continue
            if price >= min_price and volume >= min_volume:
                total_tickers_seen.add(ticker)
            bar = {
                "date": date_str,
                "open": r.get("o", 0),
                "high": r.get("h", 0),
                "low": r.get("l", 0),
                "close": price,
                "volume": volume,
                "time": date_str,
            }
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append(bar)

    # Filtere Ticker mit genug History
    valid_tickers = {t: bars for t, bars in ticker_history.items()
                     if len(bars) >= (window_size + 5)}

    # Sortiere nach avg Volumen (aktivste zuerst)
    ticker_avg_vol = {}
    for t, bars_list in valid_tickers.items():
        avg_vol = sum(b["volume"] for b in bars_list[-20:]) / 20
        ticker_avg_vol[t] = avg_vol

    tickers_to_test = sorted(ticker_avg_vol.keys(),
                              key=lambda t: ticker_avg_vol[t], reverse=True)[:max_tickers]

    # ============================================================
    # PASS 2: Rolling-Window Analyse + Trade Simulation
    # ============================================================
    all_trades = []
    signals_found = 0
    cooldown = {}  # ticker → last signal idx (min 5 Tage Abstand)

    for t_idx, ticker in enumerate(tickers_to_test):
        if progress_callback and t_idx % 10 == 0:
            progress_callback(
                0.5 + (t_idx / len(tickers_to_test)) * 0.5,
                f" Analysiere {ticker} ({t_idx+1}/{len(tickers_to_test)}) | {signals_found} Signale"
            )

        bars = ticker_history[ticker]

        for idx in range(window_size, len(bars)):
            if bars[idx]["date"] < test_start:
                continue

            # Cooldown: Min 5 Tage zwischen Signalen pro Ticker
            if ticker in cooldown:
                if idx - cooldown[ticker] < 5:
                    continue

            # 50-Bar Rolling Window
            window = bars[idx-window_size:idx]
            if (
                float(window[-1].get("close") or 0) < min_price
                or float(window[-1].get("volume") or 0) < min_volume
            ):
                continue

            # === BIOTECH TECHNICAL SCORE (offline) ===
            tech_result = _compute_biotech_technical_from_bars(window)
            tech_score = tech_result["technical_score"]
            rvol = tech_result["rvol"]

            # === SIGNAL-FILTER ===
            # 1. RVOL >= 2.0 = Unusual Volume (Proxy für Catalyst-Aktivität)
            if rvol < 2.0:
                continue

            # 2. Technical Score >= 10/20 (mindestens mittlere Qualität)
            if tech_score < 10:
                continue

            # 3. Trend-Filter: SMA20 muss über SMA50 (kein Abwärtstrend)
            w_closes = [b["close"] for b in window]
            sma20 = sum(w_closes[-20:]) / 20
            sma50 = sum(w_closes[-50:]) / 50
            if sma20 <= sma50 * 0.97:
                continue  # Deutlicher Abwärtstrend → kein Entry

            # 4. Price > $2 und Volumen-Minimum nochmal prüfen
            if window[-1]["close"] < min_price:
                continue

            # === GRADING nach Technical Score + RVOL ===
            combined = tech_score + min(10, int(rvol * 2))  # Max 10 Bonus für RVOL
            if combined >= 26:
                grade = "S"
            elif combined >= 22:
                grade = "A"
            elif combined >= 18:
                grade = "B"
            else:
                grade = "C"

            # === ATR für Stop/Target Berechnung ===
            # Calculate True Range for last 10 bars
            tr_values = []
            for i, b in enumerate(window[-10:]):
                high = b["high"]
                low = b["low"]
                if i == 0:
                    # First bar: use simple range
                    tr = high - low
                else:
                    prev_close = window[-10 + i - 1]["close"]
                    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_values.append(tr)
            atr_10 = sum(tr_values) / len(tr_values)
            if atr_10 <= 0:
                continue

            # === ENTRY/STOP/TARGET BERECHNUNG ===
            # Entry: Next Day Open (Momentum-Einstieg nach Volume Spike)
            if idx >= len(bars):
                continue

            entry_bar = bars[idx]
            entry_price = entry_bar["open"]
            if entry_price <= 0:
                continue

            # Slippage: 0.1% (Biotech-Spreads sind breiter als Blue Chips)
            slippage = 0.001
            entry_price *= (1 + slippage)

            # Stop: 1.5 × ATR unter Entry (breiter wegen Biotech-Vola)
            stop_distance = atr_10 * 1.5
            stop_price = entry_price - stop_distance

            if stop_price <= 0 or stop_price >= entry_price:
                continue

            # Grade-abhängige Targets
            # S/A: TP1=2.0R, TP2=4.0R (starkes Setup → größere Targets)
            # B/C: TP1=1.5R, TP2=3.0R (schwächeres Setup → konservativere Targets)
            if grade in ("S", "A"):
                tp1_rr, tp2_rr = 2.0, 4.0
            else:
                tp1_rr, tp2_rr = 1.5, 3.0

            risk = stop_distance
            tp1_price = entry_price + risk * tp1_rr
            tp2_price = entry_price + risk * tp2_rr

            # R:R Minimum Check: use the same blended TP1/TP2 model as alerts.
            rr = (tp1_rr + tp2_rr) / 2.0
            if rr < 1.5:
                continue

            signals_found += 1
            cooldown[ticker] = idx

            # === TRADE SIMULATION (Biotech-spezifisch) ===
            max_hold = 15  # Biotech-Catalyst-Trades sind kürzer
            trade_result = {
                "ticker": ticker,
                "signal_date": bars[idx - 1]["date"],
                "grade": grade,
                "score": tech_score,
                "max_score": 20,
                "rvol": rvol,
                "direction": "LONG",
                "entry_target": round(entry_price, 4),
                "stop_target": round(stop_price, 4),
                "tp1_target": round(tp1_price, 4),
                "tp2_target": round(tp2_price, 4),
                "rr_planned": round(rr, 2),
            }

            # === TRADE MANAGEMENT ===
            exit_price = None
            exit_reason = None
            exit_date = None
            tp1_hit = False
            bars_held = 0
            current_stop = stop_price
            actual_entry = entry_price
            entry_date = entry_bar["date"]

            for day_offset in range(max_hold):
                bar_idx = idx + day_offset
                if bar_idx >= len(bars):
                    break

                future_bar = bars[bar_idx]
                bars_held += 1

                # Stop Check (konservativ: Stop hat Priorität über TP)
                if future_bar["low"] <= current_stop:
                    # Gap-through Check
                    if future_bar["open"] <= current_stop:
                        runner_exit = future_bar["open"] * (1 - slippage)
                    else:
                        runner_exit = current_stop * (1 - slippage)
                    tp1_exit = tp1_price * (1 - slippage)
                    exit_price = (tp1_exit + runner_exit) / 2 if tp1_hit else runner_exit
                    exit_reason = "BE_STOP" if tp1_hit else "STOP"
                    exit_date = future_bar["date"]
                    break

                # TP2 Check (nur wenn TP1 schon in VORHERIGEM Bar getroffen)
                if tp1_hit and future_bar["high"] >= tp2_price:
                    tp1_exit = tp1_price * (1 - slippage)
                    tp2_exit = tp2_price * (1 - slippage)
                    exit_price = (tp1_exit + tp2_exit) / 2
                    exit_reason = "TP2"
                    exit_date = future_bar["date"]
                    break

                # TP1 Check
                if not tp1_hit and future_bar["high"] >= tp1_price:
                    tp1_hit = True
                    # Trail-Stop auf 66% zwischen Entry und TP1
                    trail_level = actual_entry + (tp1_price - actual_entry) * 0.66
                    current_stop = trail_level

            # Trade-Ende: Partial-Exit oder Max Hold
            if exit_price is None:
                if tp1_hit:
                    # TP1 erreicht, TP2 nicht → 50/50 Split
                    last_idx = min(idx + max_hold - 1, len(bars) - 1)
                    tp1_exit = tp1_price * (1 - slippage)
                    close_exit = bars[last_idx]["close"]
                    be_protected = max(close_exit, actual_entry)  # BE-Schutz
                    exit_price = (tp1_exit + be_protected) / 2
                    exit_reason = "TP1_PARTIAL"
                    exit_date = bars[last_idx]["date"]
                else:
                    # Max Hold → Exit at Close
                    last_idx = min(idx + max_hold - 1, len(bars) - 1)
                    exit_price = bars[last_idx]["close"]
                    exit_reason = "MAX_HOLD"
                    exit_date = bars[last_idx]["date"]

            # === P&L BERECHNUNG ===
            if actual_entry is None or exit_price is None or actual_entry <= 0:
                trade_result["outcome"] = "NO_FILL"
                trade_result["pnl_pct"] = 0
                trade_result["r_multiple"] = 0
                trade_result["is_winner"] = False
            else:
                pnl_pct = ((exit_price - actual_entry) / actual_entry) * 100 - 0.2  # 0.1% entry + 0.1% exit fee
                risk_pct = (risk / actual_entry) * 100 if risk > 0 else 0
                r_multiple = pnl_pct / risk_pct if risk_pct > 0 else 0
                r_multiple = max(r_multiple, -2.0)  # Net of fees; cap gap-through losses.

                trade_result["actual_entry"] = round(actual_entry, 2)
                trade_result["exit_price"] = round(exit_price, 2)
                trade_result["entry_date"] = entry_date
                trade_result["exit_date"] = exit_date
                trade_result["exit_reason"] = exit_reason
                trade_result["bars_held"] = bars_held
                trade_result["tp1_hit"] = tp1_hit
                trade_result["pnl_pct"] = round(pnl_pct, 2)
                trade_result["r_multiple"] = round(r_multiple, 2)
                trade_result["is_winner"] = pnl_pct > 0
                trade_result["outcome"] = exit_reason

            all_trades.append(trade_result)

    # ============================================================
    # STATISTIKEN nach Grade
    # ============================================================
    stats_by_grade = {}
    for g in ["S", "A", "B", "C"]:
        grade_trades = [t for t in all_trades if t["grade"] == g and t.get("outcome") != "NO_FILL"]
        if not grade_trades:
            continue

        winners = [t for t in grade_trades if t["is_winner"]]
        losers = [t for t in grade_trades if not t["is_winner"]]

        total_pnl = sum(t["pnl_pct"] for t in grade_trades)
        avg_pnl = total_pnl / len(grade_trades) if grade_trades else 0
        avg_winner = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
        avg_loser = sum(t["pnl_pct"] for t in losers) / len(losers) if losers else 0
        win_rate = len(winners) / len(grade_trades) * 100 if grade_trades else 0

        gross_profit = sum(t["pnl_pct"] for t in winners)
        gross_loss = abs(sum(t["pnl_pct"] for t in losers))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0

        avg_r = sum(t["r_multiple"] for t in grade_trades) / len(grade_trades) if grade_trades else 0

        tp1_hits = sum(1 for t in grade_trades if t.get("tp1_hit", False))
        tp2_hits = sum(1 for t in grade_trades if t.get("outcome") == "TP2")

        stats_by_grade[g] = {
            "total": len(grade_trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(avg_pnl, 2),
            "avg_winner": round(avg_winner, 2),
            "avg_loser": round(avg_loser, 2),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": profit_factor,
            "avg_r": round(avg_r, 2),
            "tp1_rate": round(tp1_hits / len(grade_trades) * 100, 1) if grade_trades else 0,
            "tp2_rate": round(tp2_hits / len(grade_trades) * 100, 1) if grade_trades else 0,
        }

    filled_trades = [t for t in all_trades if t.get("outcome") != "NO_FILL"]

    # Calculate Max Drawdown from equity curve
    equity = 10000
    peak = equity
    max_dd = 0
    for trade in filled_trades:
        equity *= (1 + trade["pnl_pct"] / 100)
        peak = max(peak, equity)
        dd = ((peak - equity) / peak) * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    summary = {
        "total_signals": signals_found,
        "total_filled": len(filled_trades),
        "no_fill": len(all_trades) - len(filled_trades),
        "win_rate": round(sum(1 for t in filled_trades if t["is_winner"]) / len(filled_trades) * 100, 1) if filled_trades else 0,
        "avg_pnl": round(sum(t["pnl_pct"] for t in filled_trades) / len(filled_trades), 2) if filled_trades else 0,
        "total_pnl": round(sum(t["pnl_pct"] for t in filled_trades), 2) if filled_trades else 0,
        "max_drawdown": round(max_dd, 2),
        "n_tickers": len(tickers_to_test),
        "n_tickers_total": len(total_tickers_seen),
        "n_biotech_universe": len(biotech_set),
        "months": months,
    }

    del ticker_history

    if progress_callback:
        progress_callback(1.0, f"[OK] BioTech Backtest fertig! {signals_found} Signale, {len(filled_trades)} Trades")

    return {"trades": all_trades, "stats_by_grade": stats_by_grade, "summary": summary}


def simulate_trade(bars, signal_idx, strategy):
    """
    Simuliert einen Trade basierend auf Signal-Tag und Strategie-Regeln.
    """
    direction = strategy["direction"]
    entry_type = strategy["entry"]
    stop_pct = strategy["stop_pct"]
    tp1_rr = strategy["tp1_rr"]
    tp2_rr = strategy["tp2_rr"]
    target_rr = (float(tp1_rr) + float(tp2_rr)) / 2.0
    max_hold = strategy["max_hold_days"]
    
    signal_day = bars[signal_idx]
    
    # === ENTRY BESTIMMEN ===
    if entry_type == "next_open":
        if signal_idx + 1 >= len(bars):
            return None
        entry_price = bars[signal_idx + 1]["open"]
        trade_start_idx = signal_idx + 1
    elif entry_type == "at_close":
        entry_price = signal_day["close"]
        trade_start_idx = signal_idx + 1
    elif entry_type == "prev_high":
        if signal_idx < 1 or signal_idx + 1 >= len(bars):
            return None
        entry_price = bars[signal_idx - 1]["high"]
        trade_start_idx = signal_idx + 1
    else:
        return None
    
    if entry_price <= 0:
        return None
    
    # === SLIPPAGE: 0.05% pro Seite (realistisch für Liquid Stocks) ===
    slippage = 0.0005
    if direction == "long":
        entry_price *= (1 + slippage)  # Kaufe leicht höher
    else:
        entry_price *= (1 - slippage)  # Shorte leicht tiefer
    
    # === MINDESTENS 1 Folgetag nötig für sinnvolle Simulation ===
    if trade_start_idx >= len(bars):
        return None
    
    # === STOP & TARGETS BERECHNEN ===
    risk = entry_price * stop_pct
    
    if direction == "long":
        stop_price = entry_price - risk
        tp1_price = entry_price + risk * tp1_rr
        tp2_price = entry_price + risk * tp2_rr
        blended_target_price = entry_price + risk * target_rr
    else:  # short
        stop_price = entry_price + risk
        tp1_price = entry_price - risk * tp1_rr
        tp2_price = entry_price - risk * tp2_rr
        blended_target_price = entry_price - risk * target_rr
    initial_stop_price = stop_price
    
    # === TRADE SIMULIEREN ===
    exit_price = None
    exit_reason = None
    exit_date = None
    tp1_hit = False
    bars_held = 0
    
    for day_offset in range(max_hold):
        bar_idx = trade_start_idx + day_offset
        if bar_idx >= len(bars):
            break
        
        bar = bars[bar_idx]
        bars_held += 1
        
        if direction == "long":
            # Prüfe ob Entry überhaupt erreicht wird (bei prev_high Entry)
            if entry_type == "prev_high" and day_offset == 0:
                if bar["high"] < entry_price:
                    return None  # Entry nicht erreicht
            
            # Stop Check (Low des Tages)
            if bar["low"] <= stop_price:
                # Wenn auch TP1 an diesem Tag möglich → konservativ: Stop first
                if bar["open"] <= stop_price:
                    runner_exit = bar["open"]  # Gapped through stop
                else:
                    runner_exit = stop_price
                exit_price = (tp1_price + runner_exit) / 2 if tp1_hit else runner_exit
                exit_reason = "TP1_STOP" if tp1_hit else "STOP"
                exit_date = bar["date"]
                break
            
            # TP2 applies only to the remaining half after TP1.
            if tp1_hit and bar["high"] >= tp2_price:
                exit_price = (tp1_price + tp2_price) / 2
                exit_reason = "BLENDED_TP"
                exit_date = bar["date"]
                break
            
            # TP1 Check (muss NACH TP2-Check kommen, damit TP2 erst ab nächstem Bar feuert)
            if not tp1_hit and bar["high"] >= tp1_price:
                tp1_hit = True
                stop_price = entry_price  # Trail Stop auf Breakeven nach TP1!
        
        else:  # short
            if entry_type == "prev_high" and day_offset == 0:
                if bar["low"] > entry_price:
                    return None
            
            if bar["high"] >= stop_price:
                if bar["open"] >= stop_price:
                    runner_exit = bar["open"]
                else:
                    runner_exit = stop_price
                exit_price = (tp1_price + runner_exit) / 2 if tp1_hit else runner_exit
                exit_reason = "TP1_STOP" if tp1_hit else "STOP"
                exit_date = bar["date"]
                break
            
            # TP2 applies only to the remaining half after TP1.
            if tp1_hit and bar["low"] <= tp2_price:
                exit_price = (tp1_price + tp2_price) / 2
                exit_reason = "BLENDED_TP"
                exit_date = bar["date"]
                break
            
            # TP1 Check (muss NACH TP2-Check kommen, damit TP2 erst ab nächstem Bar feuert)
            if not tp1_hit and bar["low"] <= tp1_price:
                tp1_hit = True
                stop_price = entry_price  # Trail Stop auf Breakeven nach TP1!
    
    # Max Hold erreicht → Exit at Close
    if exit_reason is None:
        last_bar_idx = min(trade_start_idx + max_hold - 1, len(bars) - 1)
        runner_exit = bars[last_bar_idx]["close"]
        exit_price = (tp1_price + runner_exit) / 2 if tp1_hit else runner_exit
        exit_reason = "TP1+EOD" if tp1_hit else "EOD"
        exit_date = bars[last_bar_idx]["date"]
    
    # === P&L BERECHNEN ===
    if direction == "long":
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100 - 0.2  # 0.1% entry + 0.1% exit fee
    else:
        pnl_pct = ((entry_price - exit_price) / entry_price) * 100 - 0.2  # 0.1% entry + 0.1% exit fee

    risk_pct = (risk / entry_price) * 100 if risk > 0 else 0
    r_multiple = pnl_pct / risk_pct if risk_pct > 0 else 0
    
    # Cap R-Multiple: Max -2R bei Gap-Through (realistischer Slippage)
    r_multiple = max(r_multiple, -2.0)
    
    # Skip 0-Bar Trades (kein echter Trade)
    if bars_held == 0:
        return None
    
    return {
        "signal_date": signal_day["date"],
        "entry_date": bars[trade_start_idx]["date"] if trade_start_idx < len(bars) else signal_day["date"],
        "exit_date": exit_date,
        "entry_price": round(entry_price, 2),
        "stop_price": round(initial_stop_price, 2),
        "tp1_price": round(tp1_price, 2),
        "tp2_price": round(tp2_price, 2),
        "blended_target_price": round(blended_target_price, 2),
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "target_model": "50_50_tp1_tp2",
        "pnl_pct": round(pnl_pct, 2),
        "r_multiple": round(r_multiple, 2),
        "bars_held": bars_held,
        "tp1_hit": tp1_hit,
        "is_winner": pnl_pct > 0
    }


def run_full_backtest(poly_key, strategies=None, tickers=None, months=6, progress_callback=None):
    """
    Führt vollständigen Backtest über alle Strategien und Ticker durch.
    
    Args:
        poly_key: Polygon API Key
        strategies: Liste von Strategie-Namen (None = alle)
        tickers: Liste von Tickern (None = BACKTEST_UNIVERSE)
        months: Anzahl Monate zurück
        progress_callback: Funktion für Fortschrittsanzeige
    
    Returns:
        dict mit allen Ergebnissen
    """
    import time
    from datetime import datetime, timedelta
    
    if strategies is None:
        strategies = list(BACKTEST_STRATEGY_RULES.keys())
    if tickers is None:
        tickers = BACKTEST_UNIVERSE
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=months * 30 + 30)).strftime("%Y-%m-%d")  # +30 für RVOL Lookback
    
    test_start = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    
    all_results = {s: [] for s in strategies}
    ticker_data_cache = {}
    seen_signals = set()  # Dedup: max 1 Signal pro Ticker pro Tag
    
    total_tickers = len(tickers)
    skipped_no_data = 0
    skipped_too_short = 0
    total_signals = 0
    
    for t_idx, ticker in enumerate(tickers):
        if progress_callback:
            progress_callback(t_idx / total_tickers, f" {ticker} ({t_idx+1}/{total_tickers})")
        
        # Daten holen (mit Cache)
        if ticker not in ticker_data_cache:
            bars = fetch_backtest_daily_data(poly_key, ticker, start_date, end_date)
            if not bars:
                skipped_no_data += 1
                continue
            if len(bars) < 30:
                skipped_too_short += 1
                continue
            ticker_data_cache[ticker] = bars
        
        bars = ticker_data_cache[ticker]
        
        # Für jeden Tag: Metriken berechnen und Signale prüfen
        for idx in range(21, len(bars)):  # Start bei 21 für RVOL-Lookback
            if bars[idx]["date"] < test_start:
                continue
            
            metrics = compute_daily_metrics(bars, idx)
            if not metrics:
                continue
            
            # Min-Preis Filter
            if metrics["price"] <= 0:
                continue
            
            # Jede Strategie prüfen
            for strat_name in strategies:
                strat = BACKTEST_STRATEGY_RULES[strat_name]
                
                # Preis-Filter
                min_price = strat.get("min_price", 1.0)
                if metrics["price"] < min_price:
                    continue
                
                # Signal prüfen
                if check_signal(metrics, strat["signal"]):
                    # Dedup: Max 1 Trade pro Ticker pro Tag
                    dedup_key = (ticker, bars[idx]["date"])
                    if dedup_key in seen_signals:
                        continue
                    
                    total_signals += 1
                    # Trade simulieren
                    trade = simulate_trade(bars, idx, strat)
                    if trade:
                        seen_signals.add(dedup_key)
                        trade["ticker"] = ticker
                        trade["strategy"] = strat_name
                        trade["signal_change_pct"] = round(metrics["change_pct"], 2)
                        trade["signal_rvol"] = round(metrics["rvol"], 1)
                        all_results[strat_name].append(trade)
    
    if progress_callback:
        loaded = len(ticker_data_cache)
        progress_callback(1.0, f"[OK] Fertig! {loaded} geladen, {skipped_no_data} keine Daten, {skipped_too_short} zu kurz, {total_signals} Signale")
    
    return all_results


def compute_backtest_stats(trades):
    """Berechnet Performance-Statistiken für eine Liste von Trades."""
    if not trades:
        return {
            "total_trades": 0, "winners": 0, "losers": 0, "win_rate": 0,
            "avg_pnl": 0, "avg_r": 0, "best_r": 0, "worst_r": 0,
            "avg_hold": 0, "tp1_rate": 0, "tp2_rate": 0, "stop_rate": 0,
            "profit_factor": 0, "expectancy": 0, "total_r": 0
        }
    
    winners = [t for t in trades if t["is_winner"]]
    losers = [t for t in trades if not t["is_winner"]]
    
    total = len(trades)
    win_count = len(winners)
    
    avg_pnl = sum(t["pnl_pct"] for t in trades) / total
    avg_r = sum(t["r_multiple"] for t in trades) / total
    total_r = sum(t["r_multiple"] for t in trades)
    
    avg_win = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
    avg_loss = abs(sum(t["pnl_pct"] for t in losers) / len(losers)) if losers else 1
    
    gross_profit = sum(t["r_multiple"] for t in winners) if winners else 0
    gross_loss = abs(sum(t["r_multiple"] for t in losers)) if losers else 1
    
    tp2_count = sum(1 for t in trades if t["exit_reason"] == "TP2")
    tp1_eod_count = sum(1 for t in trades if t["exit_reason"] == "TP1+EOD")
    stop_count = sum(1 for t in trades if t["exit_reason"] == "STOP")
    eod_count = sum(1 for t in trades if t["exit_reason"] == "EOD")
    
    return {
        "total_trades": total,
        "winners": win_count,
        "losers": len(losers),
        "win_rate": round(win_count / total * 100, 1) if total > 0 else 0,
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_r": round(avg_r, 2),
        "total_r": round(total_r, 1),
        "best_r": round(max(t["r_multiple"] for t in trades), 2) if trades else 0,
        "worst_r": round(min(t["r_multiple"] for t in trades), 2) if trades else 0,
        "avg_hold": round(sum(t["bars_held"] for t in trades) / total, 1) if total > 0 else 0,
        "tp1_rate": round((tp2_count + tp1_eod_count) / total * 100, 1) if total > 0 else 0,
        "tp2_rate": round(tp2_count / total * 100, 1) if total > 0 else 0,
        "stop_rate": round(stop_count / total * 100, 1) if total > 0 else 0,
        "eod_rate": round(eod_count / total * 100, 1) if total > 0 else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999,
        "expectancy": round(avg_r, 2)
    }


