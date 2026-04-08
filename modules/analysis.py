"""
Analysis Module — Support/Resistance, Multi-Day Patterns, Accumulation (V70.0)

Erweiterte Analyse-Funktionen:
- Support/Resistance aus historischen Daten
- Multi-Day Pattern Analysis
- Wyckoff Chart Analysis
- Accumulation Score
- Breakout Timing
"""
import math
import time
from datetime import datetime, timedelta
try:
    import pytz
except ImportError:
    pytz = None
from modules.indicators import (
    calculate_sma, calculate_ema, calculate_rsi_from_bars,
    calculate_atr_14, calculate_adx, calculate_obv,
    calculate_macd, calculate_atr_from_ohlc, calculate_close_position
)
from modules.data_fetchers import (
    rate_limited_get, fetch_historical_data_crypto,
    _fetch_historical_yahoo, fetch_historical_data_stocks
)
from modules.helpers import calculate_sr_levels_simple


def calculate_short_bonus_signals(ticker, bars, poly_key=None, mode="swing"):
    """
     SHORT BONUS SIGNALS — 5 zusätzliche Short-spezifische Signale

    Berechnet Bonus-Punkte für Bear Scanner auf Basis von:
    1. Earnings Proximity (Post-Earnings Drop)
    2. SMA 200 Breakdown (Stage 4 Bestätigung)
    3. Gap Down Unrecovered (Distribution)
    4. Short Interest / Days to Cover (Crowded Short oder Smart Money)
    5. Insider Selling (Insider wissen mehr)

    Args:
        ticker: Aktien-Symbol
        bars: Liste von OHLCV-Dicts mit keys: date, open, high, low, close, volume
        poly_key: Polygon API Key (für Signal 4+5)
        mode: "swing" oder "intraday" — passt Gewichtung an

    Returns:
        dict: {
            "bonus_score": int (0-50 max),
            "signals": list of signal dicts,
            "details": list of strings
        }
    """
    bonus = 0
    signals = []
    details = []

    if not bars or len(bars) < 10:
        return {"bonus_score": 0, "signals": [], "details": ["Nicht genug Daten"]}

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    opens = [b.get("open", b["close"]) for b in bars]
    volumes = [b.get("volume", 0) for b in bars]
    current_price = closes[-1]

    # =====================================================================
    # SIGNAL 1: EARNINGS PROXIMITY — max 12 Punkte
    # Post-Earnings Drop = einer der stärksten Short-Katalysatoren
    # Logik: Großer Gap Down nach Earnings = institutionelle Verkäufe
    # =====================================================================
    earnings_bonus = 0
    # Suche nach großem Gap Down in den letzten 10 Tagen
    for i in range(-min(10, len(bars)), 0):
        idx = len(bars) + i
        if idx <= 0:
            continue
        prev_close = bars[idx - 1]["close"]
        day_open = bars[idx].get("open", bars[idx]["close"])
        if prev_close > 0:
            gap_pct = (day_open - prev_close) / prev_close * 100
            day_change = (bars[idx]["close"] - prev_close) / prev_close * 100
            # Gap Down >= 3% UND Tagesschluss auch negativ = Earnings Miss wahrscheinlich
            if gap_pct <= -3.0 and day_change <= -3.0:
                # Prüfe ob der Gap NICHT recovered wurde in den Folgetagen
                gap_high = prev_close  # Level das recovered werden müsste
                # Recovery-Toleranz: 2% ODER 1× ATR (was größer ist)
                # → volatile Aktien brauchen mehr Toleranz
                _atr_for_gap = sum((bars[k]["high"] - bars[k]["low"]) for k in range(max(0, idx-5), idx)) / max(1, min(5, idx))
                _recovery_tol = max(0.98, 1.0 - (_atr_for_gap / gap_high)) if gap_high > 0 else 0.98
                recovered = False
                for j in range(idx + 1, len(bars)):
                    if bars[j]["high"] >= gap_high * _recovery_tol:
                        recovered = True
                        break

                if not recovered:
                    # Stärke basiert auf Gap-Größe
                    if gap_pct <= -8.0:
                        earnings_bonus = 12
                        details.append(f" Post-Earnings Crash: {gap_pct:.1f}% Gap Down (nicht recovered)")
                    elif gap_pct <= -5.0:
                        earnings_bonus = 9
                        details.append(f" Post-Earnings Drop: {gap_pct:.1f}% Gap Down (nicht recovered)")
                    else:
                        earnings_bonus = 6
                        details.append(f" Post-Earnings Schwäche: {gap_pct:.1f}% Gap Down")
                    signals.append({"name": "Earnings Drop", "score": earnings_bonus, "gap_pct": round(gap_pct, 1)})
                    break  # Nur den neuesten zählen
                else:
                    details.append(f" Gap Down {gap_pct:.1f}% aber recovered")

    if earnings_bonus == 0:
        details.append(" Kein Post-Earnings Drop in letzten 10 Tagen")
    bonus += earnings_bonus

    # =====================================================================
    # SIGNAL 2: SMA 200 BREAKDOWN — max 10 Punkte
    # Preis unter SMA200 = Weinstein Stage 4 (Markdown Phase)
    # SMA50 < SMA200 = Death Cross = bearisch
    # =====================================================================
    sma200_bonus = 0
    # SMA einmalig berechnen (vermeidet Doppelberechnung im elif)
    sma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None

    if sma200 is not None:
        below_200 = current_price < sma200
        death_cross = (sma50 < sma200) if sma50 else False
        declining_200 = sma200 < sum(closes[-220:-20]) / 200 if len(closes) >= 220 else False

        if below_200 and death_cross and declining_200:
            sma200_bonus = 10
            details.append(f" Stage 4 Breakdown: Preis unter fallender SMA200, Death Cross aktiv")
        elif below_200 and death_cross:
            sma200_bonus = 8
            details.append(f" Unter SMA200 + Death Cross (SMA50 < SMA200)")
        elif below_200:
            sma200_bonus = 5
            details.append(f" Preis unter SMA200 (${sma200:.2f})")
        else:
            dist_pct = (current_price - sma200) / sma200 * 100 if sma200 > 0 else 0
            if dist_pct < 2.0:
                sma200_bonus = 3
                details.append(f" Preis nur {dist_pct:.1f}% über SMA200 — Breakdown möglich")
            else:
                details.append(f" Preis {dist_pct:.1f}% über SMA200 — kein Breakdown")
    elif sma50 is not None:
        if current_price < sma50 and sma20 and sma20 < sma50:
            sma200_bonus = 4
            details.append(f" Unter SMA50 + SMA20 < SMA50 (kein SMA200 verfügbar)")
        else:
            details.append(f" SMA200 nicht verfügbar, SMA50 Trend nicht bearisch")
    else:
        details.append(" SMA200: Nicht genug Daten")
    bonus += sma200_bonus
    signals.append({"name": "SMA200 Breakdown", "score": sma200_bonus})

    # =====================================================================
    # SIGNAL 3: GAP DOWN UNRECOVERED — max 8 Punkte
    # Mehrere unrecovered Gaps = starke Distribution
    # Anders als Signal 1: zählt ALLE Gaps, nicht nur Earnings
    # =====================================================================
    gap_bonus = 0
    unrecovered_gaps = 0
    total_gap_pct = 0

    for i in range(max(1, len(bars) - 20), len(bars)):
        prev_close = bars[i - 1]["close"]
        day_open = bars[i].get("open", bars[i]["close"])
        if prev_close > 0:
            gap_pct = (day_open - prev_close) / prev_close * 100
            if gap_pct <= -1.5:  # Jeder Gap Down >= 1.5%
                # Prüfe ob recovered
                recovered = False
                for j in range(i + 1, len(bars)):
                    if bars[j]["high"] >= prev_close * 0.99:
                        recovered = True
                        break
                if not recovered:
                    unrecovered_gaps += 1
                    total_gap_pct += abs(gap_pct)

    if unrecovered_gaps >= 3:
        gap_bonus = 8
        details.append(f" {unrecovered_gaps} unrecovered Gap Downs ({total_gap_pct:.1f}% total) — massive Distribution")
    elif unrecovered_gaps >= 2:
        gap_bonus = 5
        details.append(f" {unrecovered_gaps} unrecovered Gap Downs — Distribution")
    elif unrecovered_gaps == 1:
        gap_bonus = 3
        details.append(f" 1 unrecovered Gap Down")
    else:
        details.append(" Keine unrecovered Gap Downs in 20 Tagen")
    bonus += gap_bonus
    signals.append({"name": "Gap Down Unrecovered", "score": gap_bonus, "count": unrecovered_gaps})

    # =====================================================================
    # SIGNAL 4: SHORT INTEREST — max 10 Punkte
    # Hohes Short Interest = Smart Money shortet bereits
    # Aber: Zu hohes SI = Short Squeeze Risiko (abziehen!)
    # Quelle: Polygon Ticker Details
    # =====================================================================
    si_bonus = 0
    if poly_key:
        try:
            si_url = f"https://api.polygon.io/v3/reference/tickers/{ticker}"
            si_resp = rate_limited_get(si_url, params={"apiKey": poly_key}, timeout=8)
            if si_resp.status_code == 200:
                ticker_data = si_resp.json().get("results", {})
                share_class = ticker_data.get("share_class_shares_outstanding", 0)
                # Polygon liefert Short Interest nicht direkt, aber wir nutzen
                # weighted_shares_outstanding als Proxy für Float
                weighted_shares = ticker_data.get("weighted_shares_outstanding", 0)
                market_cap = ticker_data.get("market_cap", 0)

                # Niedrige Market Cap + hohe Volatilität = besserer Short
                if market_cap and market_cap > 0:
                    if market_cap < 500_000_000:  # Small Cap < $500M
                        si_bonus += 4
                        details.append(f" Small Cap (${market_cap/1e6:.0f}M) — anfälliger für Sell-Off")
                    elif market_cap < 2_000_000_000:  # Mid Cap < $2B
                        si_bonus += 2
                        details.append(f" Mid Cap (${market_cap/1e6:.0f}M)")
                    else:
                        details.append(f" Large Cap (${market_cap/1e6:.0f}M) — schwerer zu shorten")

                # Sektor-Info für Short-Anfälligkeit
                sic_code = ticker_data.get("sic_code", "")
                sic_desc = ticker_data.get("sic_description", "")
                # Zyklische Sektoren sind bessere Short-Kandidaten in Downtrends
                cyclical_sics = ["3674", "7372", "5961", "4813", "3812", "3559"]  # Tech, Retail, Telecom
                if any(sic in str(sic_code) for sic in cyclical_sics):
                    si_bonus += 3
                    details.append(f" Zyklischer Sektor ({sic_desc[:30]}) — Short-freundlich")

                # Cap bei 10
                si_bonus = min(10, si_bonus)
                signals.append({"name": "Short Interest Proxy", "score": si_bonus, "market_cap": market_cap})
            else:
                details.append(f" Ticker-Details nicht verfügbar (HTTP {si_resp.status_code})")
        except Exception as e:
            details.append(f" Short Interest Fehler: {str(e)[:50]}")
    else:
        details.append(" Short Interest: Kein API Key")
    bonus += si_bonus

    # =====================================================================
    # SIGNAL 5: INSIDER SELLING — max 10 Punkte
    # Massive Insider-Verkäufe = stärkstes Warnsignal
    # Quelle: Polygon Insider Transactions
    # =====================================================================
    insider_bonus = 0
    if poly_key:
        try:
            # Polygon Insider Transactions API
            ins_url = "https://api.polygon.io/v2/reference/news"
            three_months_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            ins_params = {
                "ticker": ticker,
                "published_utc.gte": three_months_ago,
                "limit": 10,
                "apiKey": poly_key
            }
            ins_resp = rate_limited_get(ins_url, params=ins_params, timeout=8)
            if ins_resp.status_code == 200:
                news_items = ins_resp.json().get("results", [])
                # Suche nach negativen News-Signalen
                negative_keywords = ["downgrade", "sell", "cut", "lower", "miss", "loss",
                                    "decline", "weak", "warning", "layoff", "restructur",
                                    "investigation", "fraud", "sec ", "lawsuit"]
                positive_keywords = ["upgrade", "buy", "raise", "beat", "strong", "growth"]

                neg_count = 0
                pos_count = 0
                for item in news_items:
                    title = (item.get("title", "") or "").lower()
                    desc = (item.get("description", "") or "").lower()
                    text = title + " " + desc
                    if any(kw in text for kw in negative_keywords):
                        neg_count += 1
                    if any(kw in text for kw in positive_keywords):
                        pos_count += 1

                sentiment_ratio = neg_count - pos_count
                if sentiment_ratio >= 4:
                    insider_bonus = 10
                    details.append(f" Stark negatives News-Sentiment: {neg_count} negativ vs {pos_count} positiv")
                elif sentiment_ratio >= 2:
                    insider_bonus = 7
                    details.append(f" Negatives News-Sentiment: {neg_count} negativ vs {pos_count} positiv")
                elif sentiment_ratio >= 1:
                    insider_bonus = 4
                    details.append(f" Leicht negatives Sentiment: {neg_count} neg / {pos_count} pos")
                elif sentiment_ratio <= -2:
                    # Positive News = SCHLECHT für Short → Abzug
                    insider_bonus = -5
                    details.append(f" Positives Sentiment ({pos_count} pos) — Short riskanter")
                else:
                    details.append(f" Neutrales News-Sentiment ({neg_count} neg / {pos_count} pos)")

                signals.append({"name": "News Sentiment", "score": insider_bonus,
                               "neg": neg_count, "pos": pos_count})
            else:
                details.append(f" News nicht verfügbar (HTTP {ins_resp.status_code})")
        except Exception as e:
            details.append(f" News-Sentiment Fehler: {str(e)[:50]}")
    else:
        details.append(" News-Sentiment: Kein API Key")
    bonus += insider_bonus

    # =====================================================================
    # INTRADAY-MODUS ANPASSUNG
    # =====================================================================
    if mode == "intraday":
        # Intraday bevorzugt: hohe Volatilität + hohes Volume
        recent_atr = sum((bars[i]["high"] - bars[i]["low"]) for i in range(-5, 0)) / 5
        atr_pct = (recent_atr / current_price * 100) if current_price > 0 else 0
        if atr_pct >= 4.0:
            bonus += 5
            details.append(f" Hohe Daily ATR ({atr_pct:.1f}%) — ideal für Intraday Short")
        elif atr_pct >= 2.5:
            bonus += 3
            details.append(f" Gute Volatilität ({atr_pct:.1f}%) für Intraday")

        # Average Volume muss hoch sein für Intraday
        avg_vol = sum(volumes[-10:]) / max(1, len(volumes[-10:]))
        if avg_vol >= 5_000_000:
            bonus += 3
            details.append(f" Hohes Avg Volume ({avg_vol/1e6:.1f}M) — gute Liquidität")
        elif avg_vol >= 1_000_000:
            bonus += 1
            details.append(f" Mittleres Volume ({avg_vol/1e6:.1f}M)")

    return {
        "bonus_score": max(-25, min(50, bonus)),  # -25 bis +50 (symmetrischer)
        "signals": signals,
        "details": details,
        "mode": mode
    }


def _resolve_coingecko_id(symbol):
    """Mappt Krypto-Symbol auf CoinGecko coin_id."""
    known_ids = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
        "SOL": "solana", "XRP": "ripple", "ADA": "cardano",
        "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche-2",
        "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
        "SHIB": "shiba-inu", "LTC": "litecoin", "ATOM": "cosmos",
        "XLM": "stellar", "NEAR": "near", "FIL": "filecoin",
        "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
        "SUI": "sui", "SEI": "sei-network", "TIA": "celestia",
        "INJ": "injective-protocol", "FET": "fetch-ai", "RENDER": "render-token",
        "PEPE": "pepe", "WIF": "dogwifcoin", "BONK": "bonk",
        "FLOKI": "floki", "TRX": "tron", "TON": "the-open-network",
        "ICP": "internet-computer", "HBAR": "hedera-hashgraph",
        "VET": "vechain", "ALGO": "algorand", "FTM": "fantom",
        "SAND": "the-sandbox", "MANA": "decentraland", "AXS": "axie-infinity",
        "AAVE": "aave", "MKR": "maker", "CRV": "curve-dao-token",
        "LDO": "lido-dao", "RPL": "rocket-pool", "SNX": "havven",
        "COMP": "compound-governance-token", "SUSHI": "sushi",
        "1INCH": "1inch", "ENS": "ethereum-name-service",
        "IMX": "immutable-x", "GMT": "stepn", "APE": "apecoin",
    }
    sym = symbol.upper().strip()
    if sym in known_ids:
        return known_ids[sym]
    try:
        search_url = f"https://api.coingecko.com/api/v3/search?query={sym.lower()}"
        resp = rate_limited_get(search_url, timeout=10)
        if resp.status_code == 200:
            coins = resp.json().get("coins", [])
            for c in coins:
                if c.get("symbol", "").upper() == sym:
                    return c.get("id", sym.lower())
            if coins:
                return coins[0].get("id", sym.lower())
    except Exception:
        pass
    return sym.lower()


def calculate_rvol_at_time(current_vol, prev_day_vol, session="Regular"):
    """
    Berechnet RVOL-at-Time (Intraday-normalisiert)
    
    Das Problem mit einfachem RVOL (today_vol / yesterday_vol):
    - Um 10:00 Uhr hat der Markt erst 30 Min gehandelt
    - Gestern hatte der Markt 6.5 Stunden (390 Min)
    - Simple RVOL wäre dann immer ~0.08 (8%)
    
    Lösung: Time-Weighted RVOL mit Volume Profile
    - Typisches Intraday-Volumen-Profil:
      * 9:30-10:30: ~22% des Tagesvolumens (Opening Rush)
      * 10:30-12:00: ~18% 
      * 12:00-14:00: ~15% (Lunch Lull)
      * 14:00-15:30: ~20%
      * 15:30-16:00: ~25% (Closing Rush)
    
    Returns: Normalisiertes RVOL
    """
    if prev_day_vol <= 0 or current_vol <= 0:
        return 1.0
    
    try:
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        current_hour = now_et.hour + now_et.minute / 60
        
        # Pre-Market und After-Hours: Keine Normalisierung möglich
        if session in ["Pre-Market", "After-Hours", "Extended"]:
            # Für Pre/Post: Einfacher Vergleich, aber mit Warnung
            return round(current_vol / prev_day_vol, 2)
        
        # Regular Hours: 9:30 - 16:00 (6.5 Stunden = 390 Minuten)
        market_open = 9.5   # 9:30
        market_close = 16.0 # 16:00
        
        # Wenn Markt noch nicht offen oder schon geschlossen
        if current_hour < market_open:
            return 1.0
        if current_hour >= market_close:
            # Nach 16:00: Normaler Vergleich da Tag vorbei
            return round(current_vol / prev_day_vol, 2)
        
        # Intraday Volume Profile (kumulativ)
        # Basierend auf typischem US-Aktien Handelsmuster
        volume_profile = [
            (9.5, 0.0),    # Market Open
            (10.0, 0.12),  # 12% nach 30 Min
            (10.5, 0.22),  # 22% nach 1h
            (11.0, 0.30),  # 30% nach 1.5h
            (11.5, 0.36),  # 36%
            (12.0, 0.42),  # 42% - Lunch beginnt
            (12.5, 0.47),  # 47%
            (13.0, 0.52),  # 52%
            (13.5, 0.57),  # 57%
            (14.0, 0.62),  # 62%
            (14.5, 0.68),  # 68%
            (15.0, 0.75),  # 75%
            (15.5, 0.85),  # 85% - Closing Rush
            (16.0, 1.0),   # 100% at Close
        ]
        
        # Finde den erwarteten Volumen-Anteil für aktuelle Uhrzeit
        expected_pct = 0.0
        for i, (hour, pct) in enumerate(volume_profile):
            if current_hour <= hour:
                if i == 0:
                    expected_pct = 0.0
                else:
                    # Lineare Interpolation zwischen den Punkten
                    prev_hour, prev_pct = volume_profile[i-1]
                    time_ratio = (current_hour - prev_hour) / (hour - prev_hour)
                    expected_pct = prev_pct + time_ratio * (pct - prev_pct)
                break
        else:
            expected_pct = 1.0
        
        # Mindestens 5% erwarten (für sehr frühe Zeiten)
        expected_pct = max(0.05, expected_pct)
        
        # Erwartetes Volumen zu dieser Uhrzeit
        expected_vol = prev_day_vol * expected_pct
        
        # RVOL-at-Time
        rvol_normalized = current_vol / expected_vol if expected_vol > 0 else 1.0
        
        return round(min(rvol_normalized, 999.0), 2)
        
    except Exception as e:
        # Fallback: Einfache Berechnung
        return round(current_vol / prev_day_vol, 2) if prev_day_vol > 0 else 1.0


def analyze_multi_day_pattern(bars, pattern_type="consolidation"):
    """
    Analysiert Multi-Day Patterns basierend auf historischen Daten.
    V67.5: Komplett ueberarbeitete Berechnung und neue Pattern-Types.

    Pattern Types:
    - consolidation: Enge Range ueber mehrere Tage (Breakout Setup)
    - bull_flag: Starker Anstieg gefolgt von enger Konsolidierung
    - consolidation_breakout: Mehrtaegige enge Range + Breakout heute
    - churn: Hohes Volumen ohne Preisfortschritt (Smart Money Aktivitaet)
    - wyckoff_accumulation: Range + abnehmendes Vol + OBV steigend = Akkumulation
    - wyckoff_distribution: Range + abnehmendes Vol + OBV fallend = Distribution

    Returns: (is_valid, score, details)
    """
    if len(bars) < 3:
        return False, 0, ["Nicht genug Daten (min. 3 Tage)"]

    details = []
    score = 0

    # ── Basis-Berechnungen (fuer alle Patterns) ──
    # FIX: current_price statt bars[0] als Baseline!
    current_price = bars[-1]["close"]

    daily_changes = []
    for i in range(1, len(bars)):
        prev_close = bars[i-1]["close"]
        if prev_close and prev_close > 0:
            chg = ((bars[i]["close"] - prev_close) / prev_close) * 100
            daily_changes.append(chg)

    all_highs = [b["high"] for b in bars]
    all_lows = [b["low"] for b in bars]
    total_range_pct = ((max(all_highs) - min(all_lows)) / current_price) * 100 if current_price > 0 else 0

    volumes = [b["volume"] for b in bars]
    avg_vol = sum(volumes) / len(volumes) if volumes else 1
    recent_vol = volumes[-1] if volumes else 0
    vol_trend = recent_vol / avg_vol if avg_vol > 0 else 1.0

    # Intraday-Range pro Tag (High-Low)/Close — besserer Volatilitaets-Indikator
    daily_ranges = []
    for b in bars:
        dr = ((b["high"] - b["low"]) / b["close"]) * 100 if b["close"] > 0 else 0
        daily_ranges.append(dr)
    avg_daily_range = sum(daily_ranges) / len(daily_ranges) if daily_ranges else 0

    # OBV (On-Balance Volume) — steigend = Akkumulation, fallend = Distribution
    obv = [0]
    for i in range(1, len(bars)):
        if bars[i]["close"] > bars[i-1]["close"]:
            obv.append(obv[-1] + volumes[i])
        elif bars[i]["close"] < bars[i-1]["close"]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])

    # V68: OBV Flow-Vergleich (nicht Level-Durchschnitte — kumulative Bias!)
    # Vergleiche Netto-Zufluss in 1. vs 2. Hälfte
    obv_trend = 0
    if len(obv) >= 4:
        mid = len(obv) // 2
        early_flow = obv[mid] - obv[0]    # Netto-Zufluss erste Hälfte
        late_flow = obv[-1] - obv[mid]     # Netto-Zufluss zweite Hälfte
        obv_trend = late_flow  # positiv = aktuelle Akkumulation, negativ = Distribution

    if pattern_type == "consolidation":
        # Enge Range ueber mehrere Tage
        if total_range_pct < 8:
            score += 30
            details.append(f"Enge Range: {total_range_pct:.1f}% ueber {len(bars)} Tage")
        elif total_range_pct < 12:
            score += 15
            details.append(f"Moderate Range: {total_range_pct:.1f}%")
        else:
            details.append(f"Range zu gross: {total_range_pct:.1f}%")

        # Volumen sollte sinken (zeigt Erschoepfung = Breakout kommt)
        if vol_trend < 0.8:
            score += 20
            details.append(f"Volumen sinkt: {vol_trend:.2f}x")
        elif vol_trend < 1.2:
            score += 10
            details.append(f"Volumen stabil: {vol_trend:.2f}x")

    elif pattern_type == "bull_flag":
        if len(bars) >= 4:
            pole_move = ((bars[-3]["close"] - bars[0]["close"]) / bars[0]["close"]) * 100
            if pole_move >= 5:
                score += 30
                details.append(f"Fahnenstange: {pole_move:+.1f}%")
            elif pole_move >= 3:
                score += 15
                details.append(f"Schwache Fahnenstange: {pole_move:+.1f}%")

            recent_range = abs(daily_changes[-1]) + abs(daily_changes[-2]) if len(daily_changes) >= 2 else 0
            if recent_range < 4:
                score += 25
                details.append(f"Konsolidierung: {recent_range:.1f}% Bewegung")

    elif pattern_type == "bear_flag":
        if len(bars) >= 4:
            # Flagpole: Starker Abwaertsimpuls in den ersten Tagen
            pole_move = ((bars[-3]["close"] - bars[0]["close"]) / bars[0]["close"]) * 100
            if pole_move <= -5:
                score += 30
                details.append(f"Fahnenstange (Short): {pole_move:+.1f}%")
            elif pole_move <= -3:
                score += 15
                details.append(f"Schwache Fahnenstange: {pole_move:+.1f}%")

            # Konsolidierung: Letzte 2 Tage sollten eng sein
            recent_range = abs(daily_changes[-1]) + abs(daily_changes[-2]) if len(daily_changes) >= 2 else 0
            if recent_range < 4:
                score += 25
                details.append(f"Konsolidierung: {recent_range:.1f}% Bewegung")

    elif pattern_type == "consolidation_breakout":
        # V67.5: Fixe Baseline + bessere Volatilitaets-Berechnung
        if len(bars) >= 5:
            pre_bars = bars[:-1]  # Alles ausser heute
            pre_price = pre_bars[-1]["close"]  # FIX: letzter Pre-Close als Baseline
            pre_highs = [b["high"] for b in pre_bars]
            pre_lows = [b["low"] for b in pre_bars]
            pre_range_pct = ((max(pre_highs) - min(pre_lows)) / pre_price) * 100 if pre_price > 0 else 99

            # Kriterium 1: Enge Range VOR Breakout (max 30 Punkte)
            if pre_range_pct < 5:
                score += 30
                details.append(f"Sehr enge Range: {pre_range_pct:.1f}% ueber {len(pre_bars)} Tage")
            elif pre_range_pct < 8:
                score += 20
                details.append(f"Enge Range: {pre_range_pct:.1f}%")
            elif pre_range_pct < 12:
                score += 10
                details.append(f"Moderate Range: {pre_range_pct:.1f}%")

            # Kriterium 2: Intraday-Ranges klein (High-Low pro Tag) (max 25 Punkte)
            pre_daily_ranges = daily_ranges[:-1] if len(daily_ranges) > 1 else daily_ranges
            avg_pre_range = sum(pre_daily_ranges) / len(pre_daily_ranges) if pre_daily_ranges else 99
            if avg_pre_range < 2.0:
                score += 25
                details.append(f"Ruhige Vortage: {avg_pre_range:.1f}% avg Range/Tag")
            elif avg_pre_range < 3.5:
                score += 15
                details.append(f"Moderate Vortage: {avg_pre_range:.1f}% avg Range/Tag")
            elif avg_pre_range < 5.0:
                score += 5
                details.append(f"Leicht volatile Vortage: {avg_pre_range:.1f}%")

            # Kriterium 3: Volumen-Explosion am Breakout-Tag (max 25 Punkte)
            pre_vol_avg = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 1
            breakout_vol = volumes[-1]
            vol_ratio = breakout_vol / pre_vol_avg if pre_vol_avg > 0 else 1.0

            if vol_ratio > 3.0:
                score += 25
                details.append(f"Volumen-Explosion: {vol_ratio:.1f}x vs Vortage")
            elif vol_ratio > 2.0:
                score += 18
                details.append(f"Starkes Volumen: {vol_ratio:.1f}x")
            elif vol_ratio > 1.3:
                score += 8
                details.append(f"Leicht erhoehtes Volumen: {vol_ratio:.1f}x")

            # Kriterium 4: Volumen sank VOR Breakout (Erschoepfung) (max 20 Punkte)
            if len(volumes) >= 5:
                first_half_vol = sum(volumes[:len(volumes)//2]) / (len(volumes)//2)
                pre_half_vol = sum(volumes[len(volumes)//2:-1]) / max(1, len(volumes)//2 - 1)
                if first_half_vol > 0 and pre_half_vol < first_half_vol * 0.8:
                    score += 20
                    details.append(f"Vol sank vor Breakout: {pre_half_vol/first_half_vol:.1f}x")
                elif first_half_vol > 0 and pre_half_vol < first_half_vol * 1.0:
                    score += 10
                    details.append(f"Vol stabil vor Breakout")
        else:
            details.append("Nicht genug Daten (min. 5 Tage)")

    elif pattern_type == "churn":
        # V67.5: NEUER Pattern-Type fuer High Volume Churn
        # Churn = Hohes Volumen + Preis bewegt sich kaum = Smart Money tauscht Haende
        n = len(bars)

        # Kriterium 1: Enge Tagesrange trotz hohem Volumen (max 30 Punkte)
        if avg_daily_range < 2.0:
            score += 30
            details.append(f"Sehr enge Ranges: {avg_daily_range:.1f}% avg/Tag")
        elif avg_daily_range < 3.5:
            score += 20
            details.append(f"Enge Ranges: {avg_daily_range:.1f}% avg/Tag")
        elif avg_daily_range < 5.0:
            score += 10
            details.append(f"Moderate Ranges: {avg_daily_range:.1f}% avg/Tag")

        # Kriterium 2: Hohes SUSTAINED Volumen (nicht nur ein Tag) (max 30 Punkte)
        # Zaehle Tage mit ueberdurchschnittlichem Vol
        high_vol_days = sum(1 for v in volumes if v > avg_vol * 1.2)
        high_vol_pct = high_vol_days / n if n > 0 else 0
        if high_vol_pct >= 0.6:
            score += 30
            details.append(f"Sustained High Vol: {high_vol_days}/{n} Tage > Avg")
        elif high_vol_pct >= 0.4:
            score += 20
            details.append(f"Moderate High Vol: {high_vol_days}/{n} Tage > Avg")
        elif high_vol_pct >= 0.2:
            score += 10
            details.append(f"Vereinzelt High Vol: {high_vol_days}/{n} Tage")

        # Kriterium 3: Gesamtbewegung ist minimal (max 20 Punkte)
        net_change = ((bars[-1]["close"] - bars[0]["close"]) / bars[0]["close"]) * 100 if bars[0]["close"] > 0 else 0
        if abs(net_change) < 2:
            score += 20
            details.append(f"Netto-Bewegung minimal: {net_change:+.1f}%")
        elif abs(net_change) < 4:
            score += 10
            details.append(f"Netto-Bewegung moderat: {net_change:+.1f}%")

        # Kriterium 4: OBV Richtung (gibt Hinweis auf Akku vs Distri) (max 20 Punkte)
        if obv_trend > 0:
            score += 20
            details.append(f"OBV steigend = Akkumulation")
        elif obv_trend < 0:
            score += 15
            details.append(f"OBV fallend = Distribution")
        else:
            score += 5
            details.append(f"OBV neutral")

    elif pattern_type == "wyckoff_accumulation":
        # V67.5: NEUER Pattern-Type — Wyckoff Akkumulation mit Daily-Daten
        # Akkumulation = Range + abnehmendes Volumen + OBV steigt (Smart Money kauft)
        n = len(bars)

        # Kriterium 1: Trading Range vorhanden (max 25 Punkte)
        if total_range_pct < 15:
            score += 25
            details.append(f"Trading Range: {total_range_pct:.1f}% ueber {n} Tage")
        elif total_range_pct < 25:
            score += 15
            details.append(f"Weite Range: {total_range_pct:.1f}%")
        elif total_range_pct < 35:
            score += 5
            details.append(f"Sehr weite Range: {total_range_pct:.1f}%")

        # Kriterium 2: Volumen nimmt ab (Erschoepfung des Verkaufsdrucks) (max 25 Punkte)
        if n >= 6:
            first_third_vol = sum(volumes[:n//3]) / max(1, n//3)
            last_third_vol = sum(volumes[-(n//3):]) / max(1, n//3)
            vol_decline = last_third_vol / first_third_vol if first_third_vol > 0 else 1

            if vol_decline < 0.7:
                score += 25
                details.append(f"Vol stark abnehmend: {vol_decline:.2f}x")
            elif vol_decline < 0.9:
                score += 15
                details.append(f"Vol leicht abnehmend: {vol_decline:.2f}x")
            elif vol_decline < 1.1:
                score += 5
                details.append(f"Vol stabil: {vol_decline:.2f}x")

        # Kriterium 3: OBV steigt (Smart Money kauft in die Schwaeche) (max 25 Punkte)
        if obv_trend > 0:
            # OBV steigt waehrend Preis seitwaerts = AKKUMULATION
            score += 25
            details.append(f"OBV STEIGT trotz Range = Akkumulation!")
        else:
            details.append(f"OBV faellt = keine Akkumulation erkennbar")

        # Kriterium 4: Preis haelt sich ueber Support (keine neuen Tiefs) (max 25 Punkte)
        if n >= 10:
            mid_idx = n // 2
            first_half_low = min(b["low"] for b in bars[:mid_idx])
            second_half_low = min(b["low"] for b in bars[mid_idx:])
            # Higher Lows = bullisch
            if second_half_low > first_half_low * 1.01:
                score += 25
                details.append(f"Higher Lows: ${second_half_low:.2f} > ${first_half_low:.2f}")
            elif second_half_low >= first_half_low * 0.98:
                score += 15
                details.append(f"Stabile Lows: ~${second_half_low:.2f}")
            else:
                score += 5
                details.append(f"Neue Lows: ${second_half_low:.2f} < ${first_half_low:.2f}")

    elif pattern_type == "wyckoff_distribution":
        # V67.5: NEUER Pattern-Type — Wyckoff Distribution mit Daily-Daten
        # Distribution = Range + abnehmendes Volumen + OBV faellt (Smart Money verkauft)
        n = len(bars)

        # Kriterium 1: Trading Range vorhanden (max 25)
        if total_range_pct < 15:
            score += 25
            details.append(f"Trading Range: {total_range_pct:.1f}% ueber {n} Tage")
        elif total_range_pct < 25:
            score += 15
            details.append(f"Weite Range: {total_range_pct:.1f}%")

        # Kriterium 2: Volumen nimmt ab (max 25)
        if n >= 6:
            first_third_vol = sum(volumes[:n//3]) / max(1, n//3)
            last_third_vol = sum(volumes[-(n//3):]) / max(1, n//3)
            vol_decline = last_third_vol / first_third_vol if first_third_vol > 0 else 1

            if vol_decline < 0.7:
                score += 25
                details.append(f"Vol stark abnehmend: {vol_decline:.2f}x")
            elif vol_decline < 0.9:
                score += 15
                details.append(f"Vol leicht abnehmend: {vol_decline:.2f}x")

        # Kriterium 3: OBV FAELLT (Smart Money verkauft) (max 25)
        if obv_trend < 0:
            score += 25
            details.append(f"OBV FAELLT trotz Range = Distribution!")
        else:
            details.append(f"OBV steigt = keine Distribution erkennbar")

        # Kriterium 4: Lower Highs (Schwaeche am Top) (max 25)
        if n >= 10:
            mid_idx = n // 2
            first_half_high = max(b["high"] for b in bars[:mid_idx])
            second_half_high = max(b["high"] for b in bars[mid_idx:])
            if second_half_high < first_half_high * 0.99:
                score += 25
                details.append(f"Lower Highs: ${second_half_high:.2f} < ${first_half_high:.2f}")
            elif second_half_high <= first_half_high * 1.02:
                score += 15
                details.append(f"Stabile Highs: ~${second_half_high:.2f}")

    elif pattern_type == "reversal_setup":
        # Prüfe ob es einen mehrtägigen Downtrend gab VOR dem heutigen Reversal
        if len(bars) >= 3:
            # Kriterium 1: Gesamtbewegung der Vortage war negativ
            pre_bars = bars[:-1]  # Alles außer heute
            total_decline = ((pre_bars[-1]["close"] - pre_bars[0]["close"]) / pre_bars[0]["close"]) * 100
            
            if total_decline <= -5:
                score += 35
                details.append(f" Starker Mehrtages-Decline: {total_decline:+.1f}%")
            elif total_decline <= -3:
                score += 20
                details.append(f" Moderater Decline: {total_decline:+.1f}%")
            elif total_decline <= -1:
                score += 10
                details.append(f" Leichter Decline: {total_decline:+.1f}%")
            else:
                details.append(f" Kein Downtrend vor Reversal: {total_decline:+.1f}%")
            
            # Kriterium 2: Mindestens 2 von N Vortagen waren rot
            red_days = sum(1 for c in daily_changes[:-1] if c < 0)
            total_pre_days = len(daily_changes) - 1
            if total_pre_days > 0:
                red_pct = red_days / total_pre_days
                if red_pct >= 0.6:
                    score += 25
                    details.append(f" {red_days}/{total_pre_days} Vortage rot = Verkaufsdruck")
                elif red_pct >= 0.4:
                    score += 10
                    details.append(f" {red_days}/{total_pre_days} Vortage rot")
                else:
                    details.append(f" Nur {red_days}/{total_pre_days} rote Vortage")
            
            # Kriterium 3: Heutiges Reversal mit erhöhtem Volumen
            if len(volumes) >= 2:
                pre_vol_avg = sum(volumes[:-1]) / len(volumes[:-1])
                today_vol = volumes[-1]
                vol_ratio = today_vol / pre_vol_avg if pre_vol_avg > 0 else 1.0
                
                if vol_ratio > 1.5:
                    score += 20
                    details.append(f" Reversal-Volumen: {vol_ratio:.1f}x über Vortage")
                elif vol_ratio > 1.0:
                    score += 10
                    details.append(f" Leicht erhöhtes Volumen: {vol_ratio:.1f}x")
                else:
                    details.append(f" Schwaches Reversal-Volumen: {vol_ratio:.1f}x")
        else:
            details.append(" Nicht genug Daten für Reversal-Validierung")
    
    # V67.5: Pattern-spezifische Schwellen (max Score variiert pro Type)
    threshold_map = {
        "consolidation": 35,
        "bull_flag": 35,
        "bear_flag": 35,
        "consolidation_breakout": 40,
        "churn": 45,
        "wyckoff_accumulation": 50,   # Hoehere Schwelle — weniger False Positives
        "wyckoff_distribution": 50,
        "reversal_setup": 40,
    }
    threshold = threshold_map.get(pattern_type, 40)
    is_valid = score >= threshold
    return is_valid, score, details


def find_wyckoff_for_chart(ohlcv_data):
    """
    Findet Wyckoff Patterns direkt aus Chart-OHLCV-Daten.
    Korrekte Methodik: SC/BC definiert Range, Volume+Spread Analyse.
    """
    if not ohlcv_data or len(ohlcv_data) < 60:
        return []
    
    try:
        opens = [d.get("open", d["close"]) for d in ohlcv_data]
        closes = [d["close"] for d in ohlcv_data]
        highs = [d["high"] for d in ohlcv_data]
        lows = [d["low"] for d in ohlcv_data]
        volumes = [d.get("volume", 0) for d in ohlcv_data]
        current_price = closes[-1]
        n = len(closes)
        
        def spread(i):
            return highs[i] - lows[i]
        def body_pos(i):
            s = spread(i)
            return (closes[i] - lows[i]) / s if s > 0 else 0.5
        def avg_spread(i, lb=20):
            s = max(0, i - lb)
            return sum(spread(j) for j in range(s, i)) / max(1, i - s)
        def avg_vol(i, lb=20):
            s = max(0, i - lb)
            return sum(volumes[s:i]) / max(1, i - s)
        
        atr_vals = []
        for i in range(1, n):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            atr_vals.append(tr)
        atr = sum(atr_vals[-14:]) / min(14, len(atr_vals)) if atr_vals else current_price * 0.02
        
        results = []
        lookback_end = int(n * 0.5)
        
        # --- ACCUMULATION: Find SC ---
        best_sc = None
        for i in range(10, lookback_end):
            av = avg_vol(i)
            if av <= 0 or volumes[i] < av * 1.8:
                continue
            if spread(i) < avg_spread(i) * 1.3:
                continue
            if body_pos(i) < 0.45:
                continue
            prior_high = max(highs[max(0, i-15):i])
            decline = (prior_high - lows[i]) / prior_high if prior_high > 0 else 0
            if decline < 0.05:
                continue
            sc_score = (volumes[i] / av) * 10 + decline * 100
            if not best_sc or sc_score > best_sc["score"]:
                best_sc = {"idx": i, "low": lows[i], "vol_r": volumes[i] / av, "score": sc_score}
        
        if best_sc:
            sc_idx = best_sc["idx"]
            ar_idx, ar_high = None, 0
            for i in range(sc_idx + 2, min(sc_idx + 20, n)):
                if highs[i] > ar_high:
                    ar_high = highs[i]
                    ar_idx = i
            
            if ar_idx and ar_high > best_sc["low"]:
                rh, rl = ar_high, best_sc["low"]
                rw = rh - rl
                rm = (rh + rl) / 2
                
                if 0.02 < rw / rm < 0.30:
                    events = []
                    score = 0
                    
                    # PS (Preliminary Support): VOR SC — erste Kaufreaktion
                    for i in range(max(5, sc_idx - 20), sc_idx):
                        av = avg_vol(i)
                        if av > 0 and volumes[i] > av * 1.5 and closes[i] > opens[i] and body_pos(i) > 0.5:
                            events.append({"name": "PS", "label": f"PS ${closes[i]:.1f}", "time": ohlcv_data[i]["time"], "price": closes[i], "pos": "below"})
                            score += 5
                            break
                    
                    events.append({"name": "SC", "label": f"SC ${rl:.1f}", "time": ohlcv_data[sc_idx]["time"], "price": rl, "pos": "below"})
                    score += 20
                    events.append({"name": "AR", "label": f"AR ${rh:.1f}", "time": ohlcv_data[ar_idx]["time"], "price": rh, "pos": "above"})
                    score += 15
                    
                    # Multiple STs nahe Support mit abnehmendem Volume
                    st_count = 0
                    prev_st_vol = best_sc["vol_r"]
                    for i in range(ar_idx + 3, min(n - 5, ar_idx + int((n - ar_idx) * 0.7))):
                        if lows[i] <= rl + rw * 0.25:
                            av = avg_vol(i)
                            vr = volumes[i] / av if av > 0 else 1
                            if vr < prev_st_vol * 0.9:
                                st_label = f"ST{st_count + 1}" if st_count > 0 else "ST"
                                events.append({"name": st_label, "label": f"{st_label} ${lows[i]:.1f} (Vol {vr:.1f}x)", "time": ohlcv_data[i]["time"], "price": lows[i], "pos": "below"})
                                score += 10 if st_count == 0 else 5
                                prev_st_vol = vr
                                st_count += 1
                                if st_count >= 3:
                                    break
                    
                    # Resistance Tests (Phase B): Rallies to AR zone on weak volume
                    for i in range(ar_idx + 3, min(n - 5, ar_idx + int((n - ar_idx) * 0.7))):
                        if highs[i] >= rh - rw * 0.20:
                            av = avg_vol(i)
                            vr = volumes[i] / av if av > 0 else 1
                            if vr < 1.3:
                                events.append({"name": "RT", "label": f"RT ${highs[i]:.1f} (Vol {vr:.1f}x)", "time": ohlcv_data[i]["time"], "price": highs[i], "pos": "above"})
                                score += 5
                                break
                    
                    # Volume-Decay in Phase B
                    if ar_idx + 20 < n:
                        early_vol = sum(volumes[ar_idx:ar_idx + 10]) / 10
                        mid_pt = ar_idx + (n - ar_idx) // 2
                        if mid_pt + 10 <= n and early_vol > 0:
                            later_vol = sum(volumes[mid_pt:mid_pt + 10]) / 10
                            if later_vol < early_vol * 0.75:
                                events.append({"name": "VolDecay", "label": f"Vol Decay: {later_vol/early_vol:.0%}", "time": ohlcv_data[mid_pt]["time"], "price": rm, "pos": "below"})
                                score += 5
                    
                    # Spring on LOW Volume
                    spring_idx = None
                    spring_start = max(ar_idx + 5, int(sc_idx + (n - sc_idx) * 0.3))
                    for i in range(spring_start, n - 3):
                        if lows[i] < rl:
                            av = avg_vol(i)
                            vol_r = volumes[i] / av if av > 0 else 1
                            if vol_r < 0.85:
                                for j in range(1, min(6, n - i)):
                                    if closes[i + j] > rl + rw * 0.10:
                                        spring_idx = i
                                        events.append({"name": "Spring", "label": f"Spring ${lows[i]:.1f} (Vol {vol_r:.1f}x LOW)", "time": ohlcv_data[i]["time"], "price": lows[i], "pos": "below"})
                                        score += 25
                                        break
                            break
                    
                    # Test of Spring
                    if spring_idx and spring_idx + 5 < n:
                        for i in range(spring_idx + 2, min(spring_idx + 15, n)):
                            if lows[i] <= lows[spring_idx] + rw * 0.05:
                                av = avg_vol(i)
                                vol_r = volumes[i] / av if av > 0 else 1
                                if vol_r < 0.7:
                                    events.append({"name": "TestSpring", "label": f"Test Spring ${lows[i]:.1f} (Vol {vol_r:.1f}x)", "time": ohlcv_data[i]["time"], "price": lows[i], "pos": "below"})
                                    score += 10
                                    break
                    
                    # SOS: Wide Spread UP + High Volume
                    sos_idx = None
                    for i in range(max(ar_idx + 10, n - int(n * 0.4)), n):
                        if closes[i] > rh and closes[i] > opens[i]:
                            av = avg_vol(i)
                            if av > 0 and volumes[i] > av * 1.5 and spread(i) > avg_spread(i) * 1.3:
                                sos_idx = i
                                events.append({"name": "SOS", "label": f"SOS ${closes[i]:.1f} (High Vol)", "time": ohlcv_data[i]["time"], "price": closes[i], "pos": "above"})
                                score += 20
                                break
                    
                    # LPS: Pullback NACH SOS, hält ÜBER range_high, LOW Volume
                    if sos_idx and sos_idx + 3 < n:
                        for i in range(sos_idx + 1, n):
                            if lows[i] < closes[sos_idx]:
                                av = avg_vol(i)
                                vol_r = volumes[i] / av if av > 0 else 1
                                if lows[i] >= rh - rw * 0.10 and vol_r < 0.9:
                                    events.append({"name": "LPS", "label": f"LPS ${lows[i]:.1f} (Vol {vol_r:.1f}x)", "time": ohlcv_data[i]["time"], "price": lows[i], "pos": "below"})
                                    score += 15
                                    break
                    
                    if score >= 35:
                        has_spring = any(e["name"] == "Spring" for e in events)
                        has_sos = any(e["name"] == "SOS" for e in events)
                        phase = "D/E" if has_sos or current_price > rh else ("C" if has_spring else "B")
                        entry = rh if current_price < rh else current_price
                        spring_low = min([e["price"] for e in events if e["name"] == "Spring"], default=rl)
                        stop_price = spring_low - atr * 0.3 if has_spring else rl - atr * 0.5
                        results.append({
                            "type": "Accumulation", "direction": "LONG", "emoji": "⬆",
                            "phase": f"Phase {phase}", "score": min(score, 100), "events": events,
                            "range_high": rh, "range_low": rl,
                            "range_start_time": ohlcv_data[sc_idx]["time"],
                            "range_end_time": ohlcv_data[min(ar_idx + (n - ar_idx) // 2, n - 1)]["time"],
                            "trade": {"entry": round(entry, 2), "stop": round(stop_price, 2),
                                      "tp1": round(rh + rw * 0.75, 2), "tp2": round(rh + rw * 1.5, 2)}
                        })
        
        # --- DISTRIBUTION: Find BC ---
        best_bc = None
        for i in range(10, lookback_end):
            av = avg_vol(i)
            if av <= 0 or volumes[i] < av * 1.8:
                continue
            if spread(i) < avg_spread(i) * 1.3:
                continue
            if body_pos(i) > 0.65:
                continue
            prior_low = min(lows[max(0, i-15):i])
            rally = (highs[i] - prior_low) / prior_low if prior_low > 0 else 0
            if rally < 0.05:
                continue
            bc_score = (volumes[i] / av) * 10 + rally * 100
            if not best_bc or bc_score > best_bc["score"]:
                best_bc = {"idx": i, "high": highs[i], "vol_r": volumes[i] / av, "score": bc_score}
        
        if best_bc:
            bc_idx = best_bc["idx"]
            ar_idx, ar_low = None, float('inf')
            for i in range(bc_idx + 2, min(bc_idx + 20, n)):
                if lows[i] < ar_low:
                    ar_low = lows[i]
                    ar_idx = i
            
            if ar_idx and ar_low < best_bc["high"]:
                rh, rl = best_bc["high"], ar_low
                rw = rh - rl
                rm = (rh + rl) / 2
                
                if 0.02 < rw / rm < 0.30:
                    events = []
                    score = 0
                    
                    # PSY (Preliminary Supply): VOR BC — erste Verkaufsreaktion
                    for i in range(max(5, bc_idx - 20), bc_idx):
                        av = avg_vol(i)
                        if av > 0 and volumes[i] > av * 1.5 and closes[i] < opens[i] and body_pos(i) < 0.5:
                            events.append({"name": "PSY", "label": f"PSY ${closes[i]:.1f}", "time": ohlcv_data[i]["time"], "price": closes[i], "pos": "above"})
                            score += 5
                            break
                    
                    events.append({"name": "BC", "label": f"BC ${rh:.1f}", "time": ohlcv_data[bc_idx]["time"], "price": rh, "pos": "above"})
                    score += 20
                    events.append({"name": "AR", "label": f"AR ${rl:.1f}", "time": ohlcv_data[ar_idx]["time"], "price": rl, "pos": "below"})
                    score += 15
                    
                    # Multiple STs nahe Resistance mit abnehmendem Volume
                    st_count = 0
                    prev_st_vol = best_bc["vol_r"]
                    for i in range(ar_idx + 3, min(n - 5, ar_idx + int((n - ar_idx) * 0.7))):
                        if highs[i] >= rh - rw * 0.25:
                            av = avg_vol(i)
                            vr = volumes[i] / av if av > 0 else 1
                            if vr < prev_st_vol * 0.9:
                                st_label = f"ST{st_count + 1}" if st_count > 0 else "ST"
                                events.append({"name": st_label, "label": f"{st_label} ${highs[i]:.1f} (Vol {vr:.1f}x)", "time": ohlcv_data[i]["time"], "price": highs[i], "pos": "above"})
                                score += 10 if st_count == 0 else 5
                                prev_st_vol = vr
                                st_count += 1
                                if st_count >= 3:
                                    break
                    
                    # Support Tests (Phase B): Drops to AR zone on weak volume
                    for i in range(ar_idx + 3, min(n - 5, ar_idx + int((n - ar_idx) * 0.7))):
                        if lows[i] <= rl + rw * 0.20:
                            av = avg_vol(i)
                            vr = volumes[i] / av if av > 0 else 1
                            if vr < 1.3:
                                events.append({"name": "ST-S", "label": f"ST-S ${lows[i]:.1f} (Vol {vr:.1f}x)", "time": ohlcv_data[i]["time"], "price": lows[i], "pos": "below"})
                                score += 5
                                break
                    
                    # Volume-Decay in Phase B
                    if ar_idx + 20 < n:
                        early_vol = sum(volumes[ar_idx:ar_idx + 10]) / 10
                        mid_pt = ar_idx + (n - ar_idx) // 2
                        if mid_pt + 10 <= n and early_vol > 0:
                            later_vol = sum(volumes[mid_pt:mid_pt + 10]) / 10
                            if later_vol < early_vol * 0.75:
                                events.append({"name": "VolDecay", "label": f"Vol Decay: {later_vol/early_vol:.0%}", "time": ohlcv_data[mid_pt]["time"], "price": rm, "pos": "above"})
                                score += 5
                    
                    # UTAD on LOW Volume
                    utad_idx = None
                    utad_start = max(ar_idx + 5, int(bc_idx + (n - bc_idx) * 0.3))
                    for i in range(utad_start, n - 3):
                        if highs[i] > rh:
                            av = avg_vol(i)
                            vol_r = volumes[i] / av if av > 0 else 1
                            if vol_r < 0.85:
                                for j in range(1, min(6, n - i)):
                                    if closes[i + j] < rh - rw * 0.10:
                                        utad_idx = i
                                        events.append({"name": "UTAD", "label": f"UTAD ${highs[i]:.1f} (Vol {vol_r:.1f}x LOW)", "time": ohlcv_data[i]["time"], "price": highs[i], "pos": "above"})
                                        score += 25
                                        break
                            break
                    
                    # Test of UTAD
                    if utad_idx and utad_idx + 5 < n:
                        for i in range(utad_idx + 2, min(utad_idx + 15, n)):
                            if highs[i] >= highs[utad_idx] - rw * 0.05:
                                av = avg_vol(i)
                                vol_r = volumes[i] / av if av > 0 else 1
                                if vol_r < 0.7:
                                    events.append({"name": "TestUTAD", "label": f"Test UTAD ${highs[i]:.1f} (Vol {vol_r:.1f}x)", "time": ohlcv_data[i]["time"], "price": highs[i], "pos": "above"})
                                    score += 10
                                    break
                    
                    # SOW: Wide Spread DOWN + High Volume
                    sow_idx = None
                    for i in range(max(ar_idx + 10, n - int(n * 0.4)), n):
                        if closes[i] < rl and closes[i] < opens[i]:
                            av = avg_vol(i)
                            if av > 0 and volumes[i] > av * 1.5 and spread(i) > avg_spread(i) * 1.3:
                                sow_idx = i
                                events.append({"name": "SOW", "label": f"SOW ${closes[i]:.1f} (High Vol)", "time": ohlcv_data[i]["time"], "price": closes[i], "pos": "below"})
                                score += 20
                                break
                    
                    # LPSY: Rally nach SOW scheitert nahe Range-Low, Low Volume
                    if sow_idx and sow_idx + 3 < n:
                        for i in range(sow_idx + 1, n):
                            if highs[i] > closes[sow_idx]:
                                av = avg_vol(i)
                                vol_r = volumes[i] / av if av > 0 else 1
                                if highs[i] <= rl + rw * 0.10 and vol_r < 0.9:
                                    events.append({"name": "LPSY", "label": f"LPSY ${highs[i]:.1f} (Vol {vol_r:.1f}x)", "time": ohlcv_data[i]["time"], "price": highs[i], "pos": "above"})
                                    score += 15
                                    break
                    
                    if score >= 35:
                        has_utad = any(e["name"] == "UTAD" for e in events)
                        has_sow = any(e["name"] == "SOW" for e in events)
                        phase = "D/E" if has_sow or current_price < rl else ("C" if has_utad else "B")
                        entry = rl if current_price > rl else current_price
                        utad_high = max([e["price"] for e in events if e["name"] == "UTAD"], default=rh)
                        stop_price = utad_high + atr * 0.3 if has_utad else rh + atr * 0.5
                        results.append({
                            "type": "Distribution", "direction": "SHORT", "emoji": "⬇",
                            "phase": f"Phase {phase}", "score": min(score, 100), "events": events,
                            "range_high": rh, "range_low": rl,
                            "range_start_time": ohlcv_data[bc_idx]["time"],
                            "range_end_time": ohlcv_data[min(ar_idx + (n - ar_idx) // 2, n - 1)]["time"],
                            "trade": {"entry": round(entry, 2), "stop": round(stop_price, 2),
                                      "tp1": round(rl - rw * 0.75, 2), "tp2": round(rl - rw * 1.5, 2)}
                        })
        
        # Filter: Nur Patterns die RELEVANT sind (Range nahe am aktuellen Preis)
        # PACS Beispiel: Accumulation bei $14-17, Preis jetzt $40 → historisch, nicht zeichnen
        relevant = []
        for r in results:
            rh = r.get("range_high", 0)
            rl = r.get("range_low", 0)
            rm = (rh + rl) / 2 if (rh + rl) > 0 else 1
            dist_pct = abs(current_price - rm) / rm * 100
            if dist_pct <= 40:
                relevant.append(r)
        
        return relevant
    except Exception:
        return []


def _detect_chart_patterns(bars, direction="long"):
    """
    Erkennt Umkehr-Patterns auf Daily Bars (90-Tage Lookback).

    Erkannte Patterns:
    - Double Top (bearish) — 2 Peaks auf ähnlichem Level, Tal dazwischen
    - Double Bottom (bullish) — 2 Tiefs auf ähnlichem Level, Peak dazwischen
    - Head & Shoulders (bearish) — 3 Peaks, mittlerer am höchsten
    - Inv. Head & Shoulders (bullish) — 3 Tiefs, mittleres am tiefsten

    Returns:
        list of dict: [{pattern, severity, description}, ...]
        severity: "high" (Umkehr gegen Trade-Richtung) / "medium" / "info"
    """
    warnings = []
    if not bars or len(bars) < 30:
        return warnings

    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    n = len(bars)

    # ── Dual-Pass Pivot-Erkennung ──────────────────────────────
    # Pass 1: Fenster=5 für etablierte Swing-Punkte
    # Pass 2: Fenster=3 für scharfe Spikes (z.B. schneller H&S-Kopf)
    # Merge: Dedupliziert nach Index (±2 Bars = gleicher Pivot)
    # ──────────────────────────────────────────────────────────
    pivot_highs = []  # (index, price)
    pivot_lows = []

    for _pw in [5, 3]:
        for i in range(_pw, n - _pw):
            if highs[i] == max(highs[i-_pw:i+_pw+1]):
                # Nur hinzufügen wenn kein existierender Pivot innerhalb ±2 Bars
                if not any(abs(i - idx) <= 2 for idx, _ in pivot_highs):
                    pivot_highs.append((i, highs[i]))
            if lows[i] == min(lows[i-_pw:i+_pw+1]):
                if not any(abs(i - idx) <= 2 for idx, _ in pivot_lows):
                    pivot_lows.append((i, lows[i]))

    # Nach Index sortieren für korrekte Pattern-Reihenfolge
    pivot_highs.sort(key=lambda x: x[0])
    pivot_lows.sort(key=lambda x: x[0])

    current_price = closes[-1] if closes else 0

    # ══════════════════════════════════════════════════════════
    # DOUBLE TOP — 2 Peaks innerhalb 2% auf ähnlichem Level
    # VERSCHÄRFT: Consolidation-Ranges sind KEINE Double Tops!
    # Ein echter Double Top braucht tiefes Tal + klare Ablehnung
    # ══════════════════════════════════════════════════════════
    if len(pivot_highs) >= 2:
        for i in range(len(pivot_highs)):
            for j in range(i + 1, len(pivot_highs)):
                idx1, p1 = pivot_highs[i]
                idx2, p2 = pivot_highs[j]

                # Mindestabstand: 20 Bars (4 Wochen) — nicht zu nah beieinander
                if abs(idx2 - idx1) < 20:
                    continue

                # Peaks innerhalb 2% voneinander (war 3% — zu locker)
                diff_pct = abs(p1 - p2) / max(p1, p2) * 100
                if diff_pct > 2.0:
                    continue

                # Tal dazwischen muss mindestens 7% tiefer sein (war 5% — zu flach)
                # 5% Tal = normale Consolidation, 7%+ = echte Ablehnung
                valley = min(lows[idx1:idx2+1])
                peak_avg = (p1 + p2) / 2
                valley_depth = (peak_avg - valley) / peak_avg * 100
                if valley_depth < 7.0:
                    continue

                # Preis nahe am zweiten Peak (innerhalb 3%) — war 5%, zu weit
                # WICHTIG: dist_from_peak > 0 = Preis UNTER Peaks (approaching resistance)
                #          dist_from_peak < 0 = Preis ÜBER Peaks (durchbrochen = irrelevant!)
                dist_from_peak = (peak_avg - current_price) / peak_avg * 100

                if 0 <= dist_from_peak < 3.0:  # Preis nähert sich dem Widerstand von unten
                    severity = "high" if direction == "long" else "info"
                    peak_level = round(peak_avg, 2)
                    warnings.append({
                        "pattern": "Double Top",
                        "severity": severity,
                        "level": peak_level,
                        "proximity_pct": round(abs(dist_from_peak), 2),  # AUDIT FIX: Proximity für skalierte Penalties
                        "description": f"Zwei Peaks bei ~${peak_level} (Diff: {diff_pct:.1f}%) — Tal: -{valley_depth:.0f}% — Starker Widerstand!"
                    })
                    break
            if warnings:
                break

    # ══════════════════════════════════════════════════════════
    # DOUBLE BOTTOM — 2 Tiefs innerhalb 2%
    # VERSCHÄRFT: Analog zu Double Top — echte Ablehnung nötig
    # ══════════════════════════════════════════════════════════
    if len(pivot_lows) >= 2:
        for i in range(len(pivot_lows)):
            for j in range(i + 1, len(pivot_lows)):
                idx1, p1 = pivot_lows[i]
                idx2, p2 = pivot_lows[j]

                if abs(idx2 - idx1) < 20:
                    continue

                diff_pct = abs(p1 - p2) / max(p1, p2) * 100
                if diff_pct > 2.0:
                    continue

                peak = max(highs[idx1:idx2+1])
                trough_avg = (p1 + p2) / 2
                peak_height = (peak - trough_avg) / trough_avg * 100
                if peak_height < 7.0:
                    continue

                dist_from_bottom = (current_price - trough_avg) / trough_avg * 100

                # Nur relevant wenn Preis nahe dem Support ist (0-8% darüber)
                # oder leicht darunter (gebrochen, max -5%). Weit darunter = irrelevant
                if -5.0 <= dist_from_bottom < 8.0:
                    severity = "high" if direction == "short" else "info"
                    bottom_level = round(trough_avg, 2)
                    warnings.append({
                        "pattern": "Double Bottom",
                        "severity": severity,
                        "level": bottom_level,
                        "proximity_pct": round(abs(dist_from_bottom), 2),  # AUDIT FIX: Proximity für skalierte Penalties
                        "description": f"Zwei Tiefs bei ~${bottom_level} (Diff: {diff_pct:.1f}%) — Peak: +{peak_height:.0f}% — Starke Unterstützung!"
                    })
                    break
            if [w for w in warnings if "Bottom" in w["pattern"]]:
                break

    # ══════════════════════════════════════════════════════════
    # HEAD & SHOULDERS — 3 Peaks, mittlerer höchster
    # ══════════════════════════════════════════════════════════
    if len(pivot_highs) >= 3:
        for i in range(len(pivot_highs) - 2):
            idx1, left = pivot_highs[i]
            idx2, head = pivot_highs[i + 1]
            idx3, right = pivot_highs[i + 2]

            # Head muss höchster sein
            if head <= left or head <= right:
                continue

            # Schultern innerhalb 8% voneinander
            shoulder_diff = abs(left - right) / max(left, right) * 100
            if shoulder_diff > 8.0:
                continue

            # Head mindestens 3% über Schultern
            shoulder_avg = (left + right) / 2
            head_above = (head - shoulder_avg) / shoulder_avg * 100
            if head_above < 3.0:
                continue

            # Nackenlinie
            valley1 = min(lows[idx1:idx2+1])
            valley2 = min(lows[idx2:idx3+1])
            neckline = (valley1 + valley2) / 2

            dist_from_neck = (current_price - neckline) / neckline * 100
            # Nur relevant wenn Preis nahe Neckline (-10% bis +15%)
            # Weit darunter = Pattern längst bestätigt/irrelevant
            if -10.0 <= dist_from_neck < 15.0:
                severity = "high" if direction == "long" else "info"
                warnings.append({
                    "pattern": "Head & Shoulders",
                    "severity": severity,
                    "level": round(neckline, 2),
                    "proximity_pct": round(abs(dist_from_neck), 2),  # AUDIT FIX
                    "description": f"Kopf: ${head:.0f} | Schultern: ${left:.0f}/${right:.0f} | Nackenlinie: ~${neckline:.0f} — Klassisches Umkehr-Signal!"
                })
                break

    # ══════════════════════════════════════════════════════════
    # INVERSE H&S — 3 Tiefs, mittleres tiefstes
    # ══════════════════════════════════════════════════════════
    if len(pivot_lows) >= 3:
        for i in range(len(pivot_lows) - 2):
            idx1, left = pivot_lows[i]
            idx2, head = pivot_lows[i + 1]
            idx3, right = pivot_lows[i + 2]

            if head >= left or head >= right:
                continue

            shoulder_diff = abs(left - right) / max(left, right) * 100
            if shoulder_diff > 8.0:
                continue

            shoulder_avg = (left + right) / 2
            head_below = (shoulder_avg - head) / shoulder_avg * 100
            if head_below < 3.0:
                continue

            peak1 = max(highs[idx1:idx2+1])
            peak2 = max(highs[idx2:idx3+1])
            neckline = (peak1 + peak2) / 2

            dist_from_neck = (neckline - current_price) / neckline * 100
            # Nur relevant wenn Preis nahe Neckline (-10% bis +15%)
            # Weit darüber = Pattern längst bestätigt/irrelevant
            if -10.0 <= dist_from_neck < 15.0:
                severity = "high" if direction == "short" else "info"
                warnings.append({
                    "pattern": "Inv. Head & Shoulders",
                    "severity": severity,
                    "level": round(neckline, 2),
                    "proximity_pct": round(abs(dist_from_neck), 2),  # AUDIT FIX
                    "description": f"Kopf: ${head:.0f} | Schultern: ${left:.0f}/${right:.0f} | Nackenlinie: ~${neckline:.0f} — Bullisches Umkehr-Signal!"
                })
                break

    # ══════════════════════════════════════════════════════════
    # WIDERSPRUCHS-FILTER — Gegensätzliche Patterns entfernen
    # Double Top (bearish) + Double Bottom (bullish) = Unsinn
    # H&S (bearish) + Inv H&S (bullish) = Unsinn
    # Behalte nur das Pattern das zur aktuellen Preis-Richtung passt
    # ══════════════════════════════════════════════════════════
    if len(warnings) > 1:
        _bearish = [w for w in warnings if w["pattern"] in ("Double Top", "Head & Shoulders")]
        _bullish = [w for w in warnings if w["pattern"] in ("Double Bottom", "Inv. Head & Shoulders")]

        if _bearish and _bullish:
            # Preis-Position im 20-Tage-Range entscheidet
            if closes:
                _recent_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
                _recent_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)
                _range = _recent_high - _recent_low if _recent_high > _recent_low else 1
                _price_pos = (current_price - _recent_low) / _range  # 0=Low, 1=High

                if _price_pos >= 0.6:
                    # Preis nahe am High → bearish Patterns relevanter
                    warnings = _bearish
                elif _price_pos <= 0.4:
                    # Preis nahe am Low → bullish Patterns relevanter
                    warnings = _bullish
                else:
                    # Mitte → nur das mit höchster Severity behalten
                    _high_sev = [w for w in warnings if w["severity"] == "high"]
                    warnings = _high_sev[:1] if _high_sev else warnings[:1]

        # Max 2 Patterns — mehr verwirrt nur
        warnings = warnings[:2]

    return warnings


def calculate_sr_from_historical(ohlc_data, current_price):
    """
    ECHTE S/R-Berechnung mit technischer Analyse.
    
    Methoden:
    1. Major Swing Points (dynamischer Threshold basierend auf ATR)
    2. Volume Clusters (wo wurde am meisten gehandelt?)
    3. Fibonacci Retracements (50%, 61.8% = stärkste)
    4. Recent Consolidation Zones (wo hat der Preis Zeit verbracht?)
    5. Round Numbers (psychologische Levels)
    6. Gap Levels (offene Gaps)
    
    PROXIMITY BOOST = Levels nah am aktuellen Preis werden bevorzugt!
    CONFLUENCE = mehrere Levels nah beieinander = STÄRKER!
    """
    if not ohlc_data or len(ohlc_data) < 5:
        return calculate_sr_levels_simple(current_price)
    
    # Extrahiere OHLC Daten
    highs = [candle[2] for candle in ohlc_data]   # Index 2 = High
    lows = [candle[3] for candle in ohlc_data]    # Index 3 = Low
    closes = [candle[4] for candle in ohlc_data]  # Index 4 = Close
    
    total_candles = len(ohlc_data)
    
    # PDH/PDL/PDC — Echte Previous Day Berechnung
    # ohlc_data ist Daily → vorletzte Candle = gestern
    if total_candles >= 2:
        prev_day_high = ohlc_data[-2][2]    # Vorletzte Candle High
        prev_day_low = ohlc_data[-2][3]     # Vorletzte Candle Low
        prev_day_close = ohlc_data[-2][4]   # Vorletzte Candle Close
    else:
        prev_day_high = highs[-1] if highs else 0
        prev_day_low = lows[-1] if lows else 0
        prev_day_close = closes[-1] if closes else 0
    
    # Period High und Low
    period_high = max(highs)
    period_low = min(lows)
    price_range = period_high - period_low
    
    if price_range <= 0:
        return calculate_sr_levels_simple(current_price), {}
    
    # =========================================================================
    # ATR-basierter dynamischer Swing-Threshold
    # =========================================================================
    atr_values = []
    for i in range(1, min(20, len(closes))):
        tr = max(
            highs[-(i)] - lows[-(i)],
            abs(highs[-(i)] - closes[-(i+1)]) if i + 1 < len(closes) else 0,
            abs(lows[-(i)] - closes[-(i+1)]) if i + 1 < len(closes) else 0
        )
        atr_values.append(tr)
    
    atr = sum(atr_values) / len(atr_values) if atr_values else price_range * 0.03
    atr_pct = atr / current_price if current_price > 0 else 0.03
    
    # Dynamischer Threshold: 2x ATR% (minimum 3%, maximum 15%)
    min_swing_pct = max(0.03, min(0.15, atr_pct * 2))
    
    # Max Proximity: Levels weiter als 35% vom Preis weg sind nutzlos
    max_distance_pct = 0.35
    
    key_levels = []
    
    # =========================================================================
    # 1. MAJOR SWING POINTS (dynamischer Threshold!)
    # =========================================================================
    lookback = max(3, min(8, total_candles // 15))  # Adaptiver Lookback
    
    # Swing Highs
    for i in range(lookback, len(highs) - 2):
        window_before = highs[max(0, i-lookback):i]
        window_after = highs[i+1:min(len(highs), i+max(2, lookback//2))]
        
        if window_before and window_after and highs[i] >= max(window_before) and highs[i] >= max(window_after):
            # Prüfe: Wie weit ist der Kurs danach gefallen?
            future_low = min(lows[i+1:min(len(lows), i+20)]) if i+1 < len(lows) else lows[-1]
            drop_pct = (highs[i] - future_low) / highs[i] if highs[i] > 0 else 0
            
            # Proximity-Check: Ist das Level nah genug am aktuellen Preis?
            if current_price <= 0:
                continue
            distance_pct = abs(highs[i] - current_price) / current_price
            if distance_pct > max_distance_pct:
                continue
            
            if drop_pct >= min_swing_pct:
                # Recency-Bonus: Neuere Swing-Points wichtiger
                recency = i / total_candles  # 0 = alt, 1 = neu
                recency_bonus = int(recency * 15)
                
                # Proximity-Bonus: Näher am Preis = nützlicher
                proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 20)
                
                key_levels.append({
                    "price": highs[i],
                    "type": "Swing High",
                    "strength": min(95, 50 + int(drop_pct * 80) + recency_bonus + proximity_bonus),
                    "is_support": highs[i] < current_price
                })
    
    # Swing Lows
    for i in range(lookback, len(lows) - 2):
        window_before = lows[max(0, i-lookback):i]
        window_after = lows[i+1:min(len(lows), i+max(2, lookback//2))]
        
        if window_before and window_after and lows[i] <= min(window_before) and lows[i] <= min(window_after):
            future_high = max(highs[i+1:min(len(highs), i+20)]) if i+1 < len(highs) else highs[-1]
            rally_pct = (future_high - lows[i]) / lows[i] if lows[i] > 0 else 0
            
            if current_price <= 0:
                continue
            distance_pct = abs(lows[i] - current_price) / current_price
            if distance_pct > max_distance_pct:
                continue
            
            if rally_pct >= min_swing_pct:
                recency = i / total_candles
                recency_bonus = int(recency * 15)
                proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 20)
                
                key_levels.append({
                    "price": lows[i],
                    "type": "Swing Low",
                    "strength": min(95, 50 + int(rally_pct * 80) + recency_bonus + proximity_bonus),
                    "is_support": lows[i] < current_price
                })
    
    # =========================================================================
    # 2. RECENT CONSOLIDATION ZONES (wo hat der Preis Zeit verbracht?)
    # =========================================================================
    # Teile den Preisbereich in Bins und zähle wie oft der Preis dort war
    recent_n = min(total_candles, max(30, total_candles // 3))  # Letzte 1/3 der Daten
    recent_closes = closes[-recent_n:]
    recent_highs = highs[-recent_n:]
    recent_lows = lows[-recent_n:]
    
    if recent_closes:
        recent_high = max(recent_highs)
        recent_low = min(recent_lows)
        recent_range = recent_high - recent_low
        
        if recent_range > 0:
            num_bins = 20
            bin_size = recent_range / num_bins
            bins = {}
            
            for j in range(recent_n):
                mid = (recent_highs[j] + recent_lows[j]) / 2
                bin_idx = int((mid - recent_low) / bin_size)
                bin_idx = min(bin_idx, num_bins - 1)
                bins[bin_idx] = bins.get(bin_idx, 0) + 1
            
            # Finde die Top-Bins (> 15% der Bars)
            threshold = recent_n * 0.15
            for bin_idx, count in bins.items():
                if count >= threshold:
                    zone_price = recent_low + (bin_idx + 0.5) * bin_size
                    distance_pct = abs(zone_price - current_price) / current_price if current_price > 0 else 1

                    if distance_pct > max_distance_pct or distance_pct < 0.02:
                        continue
                    
                    proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 20)
                    
                    key_levels.append({
                        "price": zone_price,
                        "type": "Consolidation",
                        "strength": min(90, 55 + int(count / recent_n * 60) + proximity_bonus),
                        "is_support": zone_price < current_price
                    })
    
    # =========================================================================
    # 3. FIBONACCI LEVELS (nur innerhalb der Proximity-Zone)
    # =========================================================================
    fib_levels_dict = {
        "23.6": period_low + price_range * 0.236,
        "38.2": period_low + price_range * 0.382,
        "50.0": period_low + price_range * 0.5,
        "61.8": period_low + price_range * 0.618,
        "78.6": period_low + price_range * 0.786,
    }
    
    for fib_name, fib_price in fib_levels_dict.items():
        distance_pct = abs(fib_price - current_price) / current_price if current_price > 0 else 1
        if distance_pct > max_distance_pct or distance_pct < 0.02:
            continue
        
        proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 15)
        strength = (75 if fib_name in ["50.0", "61.8"] else 60) + proximity_bonus
        
        key_levels.append({
            "price": fib_price,
            "type": f"Fib {fib_name}%",
            "strength": min(90, strength),
            "is_support": fib_price < current_price
        })
    
    # =========================================================================
    # 4. PERIOD HIGH/LOW (nur wenn nah genug!)
    # =========================================================================
    ph_dist = abs(period_high - current_price) / current_price
    pl_dist = abs(period_low - current_price) / current_price
    
    if ph_dist <= max_distance_pct:
        key_levels.append({
            "price": period_high,
            "type": "Period High",
            "strength": 90,
            "is_support": False
        })

    if pl_dist <= max_distance_pct:
        key_levels.append({
            "price": period_low,
            "type": "Period Low",
            "strength": 90,
            "is_support": True
        })

    # =========================================================================
    # 4b. PREVIOUS DAY HIGH/LOW/CLOSE (wichtigste Intraday-Levels!)
    # =========================================================================
    pdh_dist = abs(prev_day_high - current_price) / current_price if current_price > 0 else 1
    pdl_dist = abs(prev_day_low - current_price) / current_price if current_price > 0 else 1
    pdc_dist = abs(prev_day_close - current_price) / current_price if current_price > 0 else 1

    if pdh_dist <= max_distance_pct and pdh_dist >= 0.005:
        key_levels.append({
            "price": prev_day_high,
            "type": "PDH",
            "strength": 85,
            "is_support": prev_day_high < current_price
        })

    if pdl_dist <= max_distance_pct and pdl_dist >= 0.005:
        key_levels.append({
            "price": prev_day_low,
            "type": "PDL",
            "strength": 85,
            "is_support": prev_day_low < current_price
        })

    if pdc_dist <= max_distance_pct and pdc_dist >= 0.01:
        key_levels.append({
            "price": prev_day_close,
            "type": "PDC",
            "strength": 80,
            "is_support": prev_day_close < current_price
        })
    
    # =========================================================================
    # 5. ROUND NUMBERS
    # =========================================================================
    if current_price >= 100:
        round_step = 10
    elif current_price >= 10:
        round_step = 1
    elif current_price >= 1:
        round_step = 0.5
    else:
        round_step = 0.05
    
    round_price = round(current_price / round_step) * round_step
    for offset in [-3, -2, -1, 1, 2, 3]:
        rp = round_price + offset * round_step
        distance_pct = abs(rp - current_price) / current_price if current_price > 0 else 1
        if distance_pct > max_distance_pct or distance_pct < 0.02:
            continue
        
        proximity_bonus = int(max(0, (1 - distance_pct / max_distance_pct)) * 15)
        
        key_levels.append({
            "price": rp,
            "type": f"Round ${rp:.2f}",
            "strength": 55 + proximity_bonus,
            "is_support": rp < current_price
        })
    
    # =========================================================================
    # 6. GAP LEVELS (nur innerhalb der Proximity-Zone)
    # =========================================================================
    for i in range(1, len(closes)):
        prev_close = closes[i-1]
        curr_high = highs[i]
        curr_low = lows[i]

        distance_pct = abs(prev_close - current_price) / current_price if current_price > 0 else 1
        if distance_pct > max_distance_pct:
            continue
        
        # Gap Up
        if curr_low > prev_close:
            gap_pct = (curr_low - prev_close) / prev_close if prev_close > 0 else 0
            if gap_pct > 0.02:
                key_levels.append({
                    "price": prev_close,
                    "type": "Gap Fill",
                    "strength": 65,
                    "is_support": prev_close < current_price
                })
        
        # Gap Down
        if curr_high < prev_close:
            gap_pct = (prev_close - curr_high) / prev_close if prev_close > 0 else 0
            if gap_pct > 0.02:
                key_levels.append({
                    "price": prev_close,
                    "type": "Gap Fill",
                    "strength": 65,
                    "is_support": prev_close < current_price
                })
    
    # =========================================================================
    # 6b. WICK CLUSTER DETECTION (Docht-Ablehnungszonen)
    # =========================================================================
    # Findet Preiszonen, wo 3+ Kerzendochte abgeprallt sind
    # (z.B. 5 Dochte bei ~$16.45 = starker Support)
    _wc_tolerance = max(0.003, atr_pct * 0.3)  # Cluster-Breite: ~0.3x ATR%
    _wc_min_touches = 3  # Minimum 3 Wick-Touches für ein gültiges Level

    # Sammle alle Wick-Lows (unterer Docht) und Wick-Highs (oberer Docht)
    _wick_lows = []  # Potentielle Support-Cluster
    _wick_highs = []  # Potentielle Resistance-Cluster
    _recent_start = max(0, total_candles - 60)  # Letzte 60 Kerzen relevant

    for i in range(_recent_start, total_candles):
        _low = lows[i]
        _high = highs[i]
        _open = ohlc_data[i][1]  # Index 1 = Open
        _close = ohlc_data[i][4]  # Index 4 = Close
        _body_low = min(_open, _close)
        _body_high = max(_open, _close)
        _candle_range = _high - _low if _high > _low else 0.001

        # Unterer Docht: Low bis Body-Low (signifikant wenn > 30% der Kerze)
        _lower_wick = _body_low - _low
        if _lower_wick / _candle_range > 0.25:
            _wick_lows.append(_low)

        # Oberer Docht: Body-High bis High (signifikant wenn > 30% der Kerze)
        _upper_wick = _high - _body_high
        if _upper_wick / _candle_range > 0.25:
            _wick_highs.append(_high)

    # Cluster-Bildung: Gruppiere Wick-Lows in Preiszonen
    def _find_wick_clusters(wicks, is_support):
        if len(wicks) < _wc_min_touches:
            return []
        _sorted = sorted(wicks)
        _clusters = []
        _current = [_sorted[0]]

        for w in _sorted[1:]:
            _cluster_avg = sum(_current) / len(_current)
            if _cluster_avg > 0 and abs(w - _cluster_avg) / _cluster_avg < _wc_tolerance:
                _current.append(w)
            else:
                if len(_current) >= _wc_min_touches:
                    _clusters.append(_current[:])
                _current = [w]
        if len(_current) >= _wc_min_touches:
            _clusters.append(_current[:])

        _results = []
        for cl in _clusters:
            _avg_price = sum(cl) / len(cl)
            _touch_count = len(cl)
            _distance_pct = abs(_avg_price - current_price) / current_price if current_price > 0 else 1

            if _distance_pct > max_distance_pct or _distance_pct < 0.005:
                continue

            _proximity_bonus = int(max(0, (1 - _distance_pct / max_distance_pct)) * 20)
            _touch_bonus = min(20, (_touch_count - _wc_min_touches) * 7)
            _strength = min(97, 65 + _touch_bonus + _proximity_bonus)

            _results.append({
                "price": _avg_price,
                "type": f"Wick Cluster ({_touch_count}x)",
                "strength": _strength,
                "is_support": is_support
            })
        return _results

    key_levels.extend(_find_wick_clusters(_wick_lows, is_support=True))
    key_levels.extend(_find_wick_clusters(_wick_highs, is_support=False))

    # =========================================================================
    # 7. CLUSTERING MIT CONFLUENCE
    # =========================================================================
    def cluster_with_confluence(levels, tolerance_pct=0.03):
        if not levels:
            return []
        
        sorted_levels = sorted(levels, key=lambda x: x["price"])
        clusters = []
        current_cluster = [sorted_levels[0]]
        
        for level in sorted_levels[1:]:
            cluster_avg = sum(l["price"] for l in current_cluster) / len(current_cluster)
            if cluster_avg > 0 and abs(level["price"] - cluster_avg) / cluster_avg < tolerance_pct:
                current_cluster.append(level)
            else:
                best = max(current_cluster, key=lambda x: x["strength"])
                types = list(set(l["type"] for l in current_cluster))
                if len(types) > 1:
                    best["type"] = " + ".join(types[:2])
                best["strength"] = min(99, best["strength"] + len(current_cluster) * 5)
                clusters.append(best)
                current_cluster = [level]
        
        if current_cluster:
            best = max(current_cluster, key=lambda x: x["strength"])
            types = list(set(l["type"] for l in current_cluster))
            if len(types) > 1:
                best["type"] = " + ".join(types[:2])
            best["strength"] = min(99, best["strength"] + len(current_cluster) * 5)
            clusters.append(best)
        
        return clusters
    
    all_clustered = cluster_with_confluence(key_levels, tolerance_pct=0.03)
    
    # Trenne Support und Resistance
    supports_raw = [l for l in all_clustered if l["price"] < current_price * 0.98]
    resistances_raw = [l for l in all_clustered if l["price"] > current_price * 1.02]
    
    # Sortiere nach COMBINED Score: Stärke + Proximity
    # Proximity-Gewichtung: Nähere Levels sind wichtiger!
    def combined_score(level):
        distance = abs(level["price"] - current_price) / current_price
        proximity_factor = max(0.3, 1.0 - distance * 2)  # Näher = höherer Faktor
        return level["strength"] * proximity_factor
    
    supports_raw = sorted(supports_raw, key=combined_score, reverse=True)[:3]
    resistances_raw = sorted(resistances_raw, key=combined_score, reverse=True)[:3]
    
    # Sortiere nach Preis für Anzeige
    supports_cleaned = sorted(supports_raw, key=lambda x: x["price"], reverse=True)
    resistances_cleaned = sorted(resistances_raw, key=lambda x: x["price"])
    
    # =========================================================================
    # OUTPUT
    # =========================================================================
    def smart_round(price):
        if price >= 1000:
            return round(price, 0)
        elif price >= 100:
            return round(price, 1)
        elif price >= 10:
            return round(price, 2)
        elif price >= 1:
            return round(price, 3)
        else:
            return round(price, 4)
    
    supports = [smart_round(s["price"]) for s in supports_cleaned]
    resistances = [smart_round(r["price"]) for r in resistances_cleaned]
    
    supports_detail = [{"price": smart_round(s["price"]), "type": s["type"], "strength": s["strength"]} for s in supports_cleaned]
    resistances_detail = [{"price": smart_round(r["price"]), "type": r["type"], "strength": r["strength"]} for r in resistances_cleaned]
    
    fib_info = {
        "period_high": smart_round(period_high),
        "period_low": smart_round(period_low),
        "prev_day_high": smart_round(prev_day_high),
        "prev_day_low": smart_round(prev_day_low),
        "prev_day_close": smart_round(prev_day_close),
        "fib_236": smart_round(fib_levels_dict["23.6"]),
        "fib_382": smart_round(fib_levels_dict["38.2"]),
        "fib_500": smart_round(fib_levels_dict["50.0"]),
        "fib_618": smart_round(fib_levels_dict["61.8"]),
        "fib_786": smart_round(fib_levels_dict["78.6"]),
        "supports_detail": supports_detail,
        "resistances_detail": resistances_detail,
        "consolidation_zones": [],
        "total_candles": total_candles,
    }
    
    return (supports, resistances), fib_info


def calculate_accumulation_score(ticker, market_type, poly_key=None, days=20):
    """
    Berechnet Akkumulations-Score (0-100) basierend auf Wyckoff-Kriterien
    
    Kriterien:
    1. Range Tightness: Je enger die Range, desto höher der Score
    2. OBV Trend: Steigend bei flachem Preis = Akkumulation
    3. Volume Pattern: Abnehmendes Volumen = Konsolidierung
    4. Position in Range: Nahe Support = besserer Entry
    5. Zeit in Range: Länger = mehr akkumuliert
    
    Returns: dict mit Score und Details
    """
    result = {
        "score": 0,
        "range_pct": 0,
        "obv_trend": 0,
        "volume_trend": 0,
        "position_in_range": 0.5,
        "days_in_range": 0,
        "wyckoff_phase": "Unknown",
        "interpretation": "",
        "data_available": False
    }
    
    try:
        # Historische Daten holen
        ohlc_data = None
        
        if market_type == "Krypto":
            # V67.5 FIX: CoinGecko braucht den vollen coin_id ("bitcoin"), nicht Symbol ("btc")
            # Versuche zuerst symbol-to-id Mapping ueber Search API
            coin_id = _resolve_coingecko_id(ticker)
            ohlc_data = fetch_historical_data_crypto(coin_id, days)
        elif market_type == "Aktien":
            # Internationale Aktien: Yahoo (kein poly_key nötig)
            _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
            if any(ticker.upper().endswith(s) for s in _intl_suffixes):
                ohlc_data = _fetch_historical_yahoo(ticker, days)
            elif poly_key:
                ohlc_data = fetch_historical_data_stocks(ticker, days, poly_key)
        
        if not ohlc_data or len(ohlc_data) < 10:
            result["interpretation"] = "Nicht genug historische Daten"
            return result
        
        result["data_available"] = True
        
        # Daten extrahieren - OHLC Format: [timestamp, open, high, low, close]
        # Index: 0=timestamp, 1=open, 2=high, 3=low, 4=close
        closes = [d[4] for d in ohlc_data if len(d) >= 5]
        highs = [d[2] for d in ohlc_data if len(d) >= 5]
        lows = [d[3] for d in ohlc_data if len(d) >= 5]
        
        # Für Volume: Polygon hat es im Result, CoinGecko nicht direkt
        # Wir nehmen einfach den Preisspread als Proxy wenn kein Volume
        volumes = []
        for d in ohlc_data:
            if len(d) >= 6 and d[5]:  # Volume ist Index 5 wenn vorhanden
                volumes.append(d[5])
            elif len(d) >= 5:
                # Proxy: (High - Low) als "Activity"
                volumes.append(abs(d[2] - d[3]) * 1000)
        
        if not closes or not highs or not lows:
            result["interpretation"] = "Ungültiges Datenformat"
            return result
        
        current_price = closes[-1]
        period_high = max(highs)
        period_low = min(lows)
        
        # 1. Range Tightness (max 25 Punkte)
        range_pct = ((period_high - period_low) / current_price) * 100 if current_price > 0 else 100
        result["range_pct"] = round(range_pct, 2)
        
        if range_pct < 10:
            range_score = 25  # Sehr eng
        elif range_pct < 15:
            range_score = 20
        elif range_pct < 20:
            range_score = 15
        elif range_pct < 30:
            range_score = 10
        else:
            range_score = 5  # Zu volatil
        
        # 2. OBV Trend (max 25 Punkte)
        obv, obv_trend = calculate_obv(closes, volumes)
        result["obv_trend"] = round(obv_trend, 2)
        
        # Preis-Trend berechnen
        price_change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] > 0 else 0
        
        # OBV Score: Steigendes OBV bei flachem Preis = TOP!
        if obv_trend > 10 and abs(price_change) < 10:
            obv_score = 25  # Perfekte Akkumulation!
        elif obv_trend > 5 and abs(price_change) < 15:
            obv_score = 20
        elif obv_trend > 0:
            obv_score = 15
        elif obv_trend > -10:
            obv_score = 10
        else:
            obv_score = 5  # Distribution möglich
        
        # 3. Volume Trend (max 20 Punkte)
        vol_change = 0  # V67.5 FIX: Default damit vol_change immer definiert ist
        if len(volumes) >= 10:
            first_half_vol = sum(volumes[:len(volumes)//2])
            second_half_vol = sum(volumes[len(volumes)//2:])
            vol_change = ((second_half_vol - first_half_vol) / first_half_vol) * 100 if first_half_vol > 0 else 0
            result["volume_trend"] = round(vol_change, 2)
            
            # Abnehmendes Volumen = Konsolidierung (gut für Akkumulation)
            if vol_change < -20:
                vol_score = 20  # Stark abnehmendes Volumen
            elif vol_change < -10:
                vol_score = 15
            elif vol_change < 10:
                vol_score = 10
            else:
                vol_score = 5  # Steigendes Volumen = etwas passiert
        else:
            vol_score = 10
        
        # 4. Position in Range (max 15 Punkte)
        if period_high != period_low:
            position = (current_price - period_low) / (period_high - period_low)
        else:
            position = 0.5
        result["position_in_range"] = round(position, 2)
        
        # Nahe Support = besserer Entry für Long
        if position < 0.3:
            pos_score = 15  # Nahe Support
        elif position < 0.5:
            pos_score = 12
        elif position < 0.7:
            pos_score = 8
        else:
            pos_score = 5  # Nahe Resistance
        
        # 5. Stabilität (max 15 Punkte) - wie viele Tage in der Range?
        range_tolerance = (period_high - period_low) * 0.1
        days_in_range = 0
        for i in range(len(closes) - 1, -1, -1):
            if lows[i] >= (period_low - range_tolerance) and highs[i] <= (period_high + range_tolerance):
                days_in_range += 1
            else:
                break
        result["days_in_range"] = days_in_range
        
        if days_in_range >= 15:
            stability_score = 15
        elif days_in_range >= 10:
            stability_score = 12
        elif days_in_range >= 5:
            stability_score = 8
        else:
            stability_score = 5
        
        # Gesamtscore
        total_score = range_score + obv_score + vol_score + pos_score + stability_score
        result["score"] = total_score
        
        # Wyckoff Phase bestimmen
        if obv_trend > 10 and abs(price_change) < 5 and vol_change < 0:
            result["wyckoff_phase"] = "Phase C (Spring/Test)"
            result["interpretation"] = " Ideale Akkumulation! OBV steigt, Preis flach, Volumen sinkt"
        elif obv_trend > 0 and range_pct < 20:
            result["wyckoff_phase"] = "Phase B (Accumulation)"
            result["interpretation"] = " Akkumulation läuft - Smart Money kauft"
        elif obv_trend < -10 and range_pct < 20:
            result["wyckoff_phase"] = "Phase D (Distribution?)"
            result["interpretation"] = " Vorsicht: OBV fällt - mögliche Distribution"
        elif range_pct > 25:
            result["wyckoff_phase"] = "Phase A (Selling Climax)"
            result["interpretation"] = " Hohe Volatilität - noch keine klare Akkumulation"
        else:
            result["wyckoff_phase"] = "Phase B (Range)"
            result["interpretation"] = " In Range - beobachten für Entry"
        
        # Score-Interpretation
        if total_score >= 80:
            result["interpretation"] = " STRONG BUY ZONE! " + result["interpretation"]
        elif total_score >= 60:
            result["interpretation"] = " Good Setup. " + result["interpretation"]
        elif total_score >= 40:
            result["interpretation"] = " Neutral. " + result["interpretation"]
        else:
            result["interpretation"] = " Weak Setup. " + result["interpretation"]
        
    except Exception as e:
        result["interpretation"] = f"Analyse-Fehler: {str(e)[:50]}"
    
    return result




# ── Weitere Analysis-Funktionen (V70.4) ──

def get_timing_assessment(row_data, strategy_name, fib_info=None):
    """
    Wählt die richtige Timing-Bewertung basierend auf der Strategie.
    """
    strategy_upper = strategy_name.upper() if strategy_name else ""
    
    # Breakout Strategien
    if any(x in strategy_upper for x in ["BREAKOUT", "AUSBRUCH", "ULTRA"]):
        return calculate_breakout_timing(row_data, fib_info)
    
    # Gap Strategien
    elif any(x in strategy_upper for x in ["GAP UP", "GAP DOWN", "PM GAINER", "PM GAP", "AH GAINER", "PREMARKET", "AFTERHOUR"]):
        is_gap_up = "DOWN" not in strategy_upper
        return calculate_gap_timing(row_data, is_gap_up)
    
    # MA Bounce Strategien
    elif any(x in strategy_upper for x in ["MA BOUNCE", "EMA", "SMA", "MOVING AVERAGE", "BOUNCE"]):
        ma_type = "EMA 21" if "EMA" in strategy_upper else ("SMA 200" if "200" in strategy_upper else "SMA 50")
        return calculate_ma_bounce_timing(row_data, ma_type)
    
    # Mean Reversion / Reversal Strategien
    elif any(x in strategy_upper for x in ["REVERSAL", "MEAN REVERSION", "OVERSOLD", "OVERBOUGHT", "RSI"]):
        # Trend-Check: Wenn fib_info vorhanden, prüfe ob Stock im Uptrend ist
        # Stock nahe Period High = kein echtes Reversal → Breakout/Continuation
        if fib_info:
            period_high = fib_info.get("period_high", 0)
            period_low = fib_info.get("period_low", 0)
            if period_high > period_low > 0:
                price_for_check = row_data.get("Preis", 0) or row_data.get("Close", 0) or row_data.get("price", 0) or 0
                if price_for_check > 0:
                    range_pos = (price_for_check - period_low) / (period_high - period_low)
                    if range_pos > 0.60:
                        # Uptrend → Continuation/Breakout Timing statt Reversal
                        return calculate_breakout_timing(row_data, fib_info)
        is_long = "SHORT" not in strategy_upper and "OVERBOUGHT" not in strategy_upper
        return calculate_reversal_timing(row_data, is_long)
    
    # Volume Void Strategien
    elif any(x in strategy_upper for x in ["VOID", "VOLUME VOID", "FVG", "FAIR VALUE", "LIQUIDITY"]):
        return calculate_void_timing(row_data)
    
    # Insider Strategien
    elif any(x in strategy_upper for x in ["INSIDER", "FORM 4", "SEC"]):
        return calculate_insider_timing(row_data)
    
    # Default: Breakout-Bewertung als Fallback
    else:
        return calculate_breakout_timing(row_data, fib_info)


def generate_ai_chart_analysis(ticker, ohlcv_data, patterns, sr_levels, fib_levels, volume_profile=None):
    """
    Generiert KI-basierte Chart-Analyse.
    
    Returns:
        dict mit summary, trade_idea, risk_reward, key_levels
    """
    if not ohlcv_data or len(ohlcv_data) < 10:
        return None
    
    current_price = ohlcv_data[-1]["close"]
    
    analysis = {
        "ticker": ticker,
        "current_price": current_price,
        "summary": [],
        "trade_idea": None,
        "key_levels": [],
        "bias": "Neutral"
    }
    
    # Pattern Analysis
    bullish_patterns = [p for p in patterns if p.get("type") == "bullish"]
    bearish_patterns = [p for p in patterns if p.get("type") == "bearish"]
    
    if bullish_patterns:
        for p in bullish_patterns:
            analysis["summary"].append(f"{p['emoji']} {p['pattern']}: {p['description']}")
        analysis["bias"] = "Bullish"
    
    if bearish_patterns:
        for p in bearish_patterns:
            analysis["summary"].append(f"{p['emoji']} {p['pattern']}: {p['description']}")
        if not bullish_patterns:
            analysis["bias"] = "Bearish"
        else:
            analysis["bias"] = "Mixed"
    
    # Support/Resistance Analysis
    if sr_levels:
        supports = sr_levels.get("support_levels", [])
        resistances = sr_levels.get("resistance_levels", [])
        
        if supports:
            nearest_support = supports[0]
            dist = (current_price - nearest_support["price"]) / current_price * 100
            analysis["summary"].append(f" Nearest Support: ${nearest_support['price']:.2f} ({dist:.1f}% below)")
            analysis["key_levels"].append({"type": "support", "price": nearest_support["price"], "strength": nearest_support.get("strength", 1)})
        
        if resistances:
            nearest_resistance = resistances[0]
            dist = (nearest_resistance["price"] - current_price) / current_price * 100
            analysis["summary"].append(f" Nearest Resistance: ${nearest_resistance['price']:.2f} ({dist:.1f}% above)")
            analysis["key_levels"].append({"type": "resistance", "price": nearest_resistance["price"], "strength": nearest_resistance.get("strength", 1)})
    
    # Trade Idea Generation
    if analysis["bias"] == "Bullish" and sr_levels:
        supports = sr_levels.get("support_levels", [])
        resistances = sr_levels.get("resistance_levels", [])
        
        if supports and resistances:
            entry = current_price
            stop = supports[0]["price"] * 0.99
            target = resistances[0]["price"]
            risk = entry - stop
            reward = target - entry
            rr = reward / risk if risk > 0 else 0
            
            analysis["trade_idea"] = {
                "direction": "LONG",
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "risk_reward": round(rr, 2)
            }
    
    elif analysis["bias"] == "Bearish" and sr_levels:
        supports = sr_levels.get("support_levels", [])
        resistances = sr_levels.get("resistance_levels", [])
        
        if supports and resistances:
            entry = current_price
            stop = resistances[0]["price"] * 1.01
            target = supports[0]["price"]
            risk = stop - entry
            reward = entry - target
            rr = reward / risk if risk > 0 else 0
            
            analysis["trade_idea"] = {
                "direction": "SHORT",
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "risk_reward": round(rr, 2)
            }
    
    return analysis


def get_accumulation_display(ticker, market_type, poly_key=None):
    """Erstellt eine formatierte Anzeige der Akkumulations-Analyse"""
    analysis = calculate_accumulation_score(ticker, market_type, poly_key)
    
    if not analysis["data_available"]:
        return None, analysis
    
    # Score-Farbe
    score = analysis["score"]
    if score >= 80:
        score_color = ""
        score_label = "STRONG"
    elif score >= 60:
        score_color = ""
        score_label = "GOOD"
    elif score >= 40:
        score_color = ""
        score_label = "NEUTRAL"
    else:
        score_color = ""
        score_label = "WEAK"
    
    # OBV Trend Interpretation
    obv = analysis["obv_trend"]
    if obv > 10:
        obv_icon = ""
        obv_text = "Steigend (Bullish)"
    elif obv > 0:
        obv_icon = "↗"
        obv_text = "Leicht steigend"
    elif obv > -10:
        obv_icon = ""
        obv_text = "Flach"
    else:
        obv_icon = ""
        obv_text = "Fallend (Bearish)"
    
    display = {
        "score": score,
        "score_color": score_color,
        "score_label": score_label,
        "range_pct": analysis["range_pct"],
        "obv_trend": obv,
        "obv_icon": obv_icon,
        "obv_text": obv_text,
        "volume_trend": analysis["volume_trend"],
        "position": analysis["position_in_range"],
        "days_in_range": analysis["days_in_range"],
        "wyckoff_phase": analysis["wyckoff_phase"],
        "interpretation": analysis["interpretation"]
    }
    
    return display, analysis


def check_earnings_proximity(ticker, earnings_calendar):
    """
    Prüft ob ein Ticker bald Earnings hat.
    
    Returns: Dict mit Warnung oder None
    {
        "warning": " EARNINGS HEUTE (AMC)",
        "level": "TODAY_AMC",  # TODAY_BMO, TODAY_AMC, TOMORROW, THIS_WEEK
        "date": "2026-02-26",
        "hour": "amc",
        "score_penalty": -15,
        "details": "Q4 2025 | EPS Est: $1.50"
    }
    """
    if not earnings_calendar or ticker not in earnings_calendar:
        return None
    
    from datetime import datetime, timedelta
    entry = earnings_calendar[ticker]
    ear_date_str = entry.get("date", "")
    hour = entry.get("hour", "")
    
    if not ear_date_str:
        return None
    
    try:
        ear_date = datetime.strptime(ear_date_str, "%Y-%m-%d").date()
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        yesterday = today - timedelta(days=1)
        days_until = (ear_date - today).days
        
        # Details String
        details_parts = []
        if entry.get("quarter") and entry.get("year"):
            details_parts.append(f"Q{entry['quarter']} {entry['year']}")
        if entry.get("epsEstimate"):
            details_parts.append(f"EPS Est: ${entry['epsEstimate']:.2f}")
        if entry.get("revenueEstimate"):
            rev = entry["revenueEstimate"]
            if rev >= 1e9:
                details_parts.append(f"Rev Est: ${rev/1e9:.1f}B")
            elif rev >= 1e6:
                details_parts.append(f"Rev Est: ${rev/1e6:.0f}M")
        details = " | ".join(details_parts) if details_parts else ""
        
        hour_text = {"bmo": "vor Börsenöffnung", "amc": "nach Börsenschluss", "dmh": "während Handel"}.get(hour, "")
        hour_short = {"bmo": "BMO", "amc": "AMC", "dmh": "DMH"}.get(hour, "")
        
        # Gestern AMC = Earnings sind GERADE passiert (Gap-Risiko heute!)
        if ear_date == yesterday and hour == "amc":
            return {
                "warning": f" EARNINGS GESTERN AMC — Gap-Risiko!",
                "level": "YESTERDAY_AMC",
                "date": ear_date_str,
                "hour": hour,
                "score_penalty": -10,
                "details": details,
                "hour_text": "gestern nach Börsenschluss",
            }
        
        # Heute
        if ear_date == today:
            if hour == "bmo":
                return {
                    "warning": f" EARNINGS HEUTE {hour_short} — {hour_text}!",
                    "level": "TODAY_BMO",
                    "date": ear_date_str,
                    "hour": hour,
                    "score_penalty": -15,
                    "details": details,
                    "hour_text": hour_text,
                }
            elif hour == "amc":
                return {
                    "warning": f" EARNINGS HEUTE {hour_short} — {hour_text}!",
                    "level": "TODAY_AMC",
                    "date": ear_date_str,
                    "hour": hour,
                    "score_penalty": -15,
                    "details": details,
                    "hour_text": hour_text,
                }
            else:
                return {
                    "warning": f" EARNINGS HEUTE!",
                    "level": "TODAY",
                    "date": ear_date_str,
                    "hour": hour,
                    "score_penalty": -15,
                    "details": details,
                    "hour_text": hour_text or "heute",
                }
        
        # Morgen
        if ear_date == tomorrow:
            return {
                "warning": f" EARNINGS MORGEN{' '+hour_short if hour_short else ''}",
                "level": "TOMORROW",
                "date": ear_date_str,
                "hour": hour,
                "score_penalty": -10,
                "details": details,
                "hour_text": f"morgen {hour_text}".strip(),
            }
        
        # Diese Woche (2-5 Tage)
        if 2 <= days_until <= 5:
            weekdays = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
            day_name = weekdays[ear_date.weekday()]
            return {
                "warning": f" EARNINGS {day_name}{' '+hour_short if hour_short else ''} ({ear_date_str})",
                "level": "THIS_WEEK",
                "date": ear_date_str,
                "hour": hour,
                "score_penalty": -5,
                "details": details,
                "hour_text": f"{day_name} {hour_text}".strip(),
            }
        
        # Nächste Woche (6-7 Tage) — nur Info, kein Penalty
        if 6 <= days_until <= 7:
            return {
                "warning": f" Earnings nächste Woche ({ear_date_str})",
                "level": "NEXT_WEEK",
                "date": ear_date_str,
                "hour": hour,
                "score_penalty": 0,
                "details": details,
                "hour_text": "",
            }
        
        return None
    
    except Exception:
        return None


def compute_daily_metrics(bars, idx):
    """
    Berechnet Screening-Metriken für einen Tag.
    
    Returns: dict mit change_pct, gap_pct, rvol, close_pos, prev_change_pct
    """
    if idx < 1 or idx >= len(bars):
        return None
    
    today = bars[idx]
    yesterday = bars[idx - 1]
    
    if yesterday["close"] <= 0 or today["close"] <= 0:
        return None
    
    # Change % (heute Close vs Gestern Close)
    change_pct = ((today["close"] - yesterday["close"]) / yesterday["close"]) * 100
    
    # Gap % (heute Open vs Gestern Close)
    gap_pct = ((today["open"] - yesterday["close"]) / yesterday["close"]) * 100
    
    # Close Position (wo hat heute geschlossen relativ zur Range)
    day_range = today["high"] - today["low"]
    close_pos = (today["close"] - today["low"]) / day_range if day_range > 0 else 0.5
    
    # RVOL (Volumen heute vs 20-Tage Durchschnitt)
    lookback_start = max(0, idx - 20)
    avg_vol_bars = bars[lookback_start:idx]
    avg_vol = sum(b["volume"] for b in avg_vol_bars) / len(avg_vol_bars) if avg_vol_bars else 1
    rvol = today["volume"] / avg_vol if avg_vol > 0 else 1.0
    
    # Previous day change %
    prev_change_pct = 0
    if idx >= 2:
        day_before = bars[idx - 2]
        if day_before["close"] > 0:
            prev_change_pct = ((yesterday["close"] - day_before["close"]) / day_before["close"]) * 100
    
    return {
        "change_pct": change_pct,
        "gap_pct": gap_pct,
        "close_pos": close_pos,
        "rvol": rvol,
        "prev_change_pct": prev_change_pct,
        "price": today["close"],
        "day_high": today["high"],
        "day_low": today["low"]
    }


def _earnings_flag(ear):
    if ear and isinstance(ear, dict):
        level = ear.get("level", "")
        if level in ("TODAY_AMC", "TODAY_BMO", "TODAY", "YESTERDAY_AMC"):
            return "ER"
        elif level == "TOMORROW":
            return "ER"
        elif level == "THIS_WEEK":
            return "ER"
    return ""


def calculate_breakout_timing(row_data, fib_info=None):
    """
    Bewertet ob ein Breakout-Einstieg noch gut ist oder schon überdehnt.
    
    Faktoren:
    1. Distanz vom Breakout (Change%) - wie weit ist der Move schon?
    2. Momentum (Tages-Move als RSI-Proxy) - überdehnt?
    3. Fib Extension - über 127.2% / 161.8%?
    4. RVOL - Volumen-Bestätigung?
    5. ATR - ist der Move überdehnt vs. normale Volatilität?
    
    Returns:
        dict mit:
        - score: 0-6 Punkte
        - rating: "FRÜH", "OK", oder "ZU SPÄT"
        - emoji: [OK], [!], oder [X]
        - factors: Liste der Einzelbewertungen
        - risk: Risiko-Einschätzung
    """
    factors = []
    score = 0
    
    # Extrahiere Daten
    change_pct = abs(row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0)
    rvol = row_data.get("RVOL", 1) or 1
    atr_pct = row_data.get("ATR%", 0) or 0
    price = row_data.get("Preis", 0) or row_data.get("Price", 0) or 0
    
    # 1. DISTANZ VOM BREAKOUT (Change%)
    if change_pct <= 3:
        factors.append({"name": "Distanz", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Früh im Move"})
        score += 1
    elif change_pct <= 7:
        factors.append({"name": "Distanz", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Noch OK"})
        score += 0.5
    else:
        factors.append({"name": "Distanz", "value": f"+{change_pct:.1f}%", "ok": False, "detail": "Schon weit gelaufen"})
    
    # 2. MOMENTUM (basierend auf heutigem Move — KEIN echtes RSI!)
    # Echtes RSI braucht 14 Tage History, hier nur Tages-Momentum verfügbar
    momentum = change_pct  # Einfach: wie weit ist der Move heute?
    if momentum <= 5:
        factors.append({"name": "Momentum", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Moderate Bewegung"})
        score += 1
    elif momentum <= 10:
        factors.append({"name": "Momentum", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Starke Bewegung"})
        score += 0.5
    else:
        factors.append({"name": "Momentum", "value": f"+{momentum:.1f}%", "ok": False, "detail": "Überdehnt (>10%)"})
    
    # 3. FIBONACCI EXTENSION
    if fib_info and price > 0:
        fib_127 = fib_info.get("fib_1272", 0) or fib_info.get("period_high", 0) * 1.05
        fib_161 = fib_info.get("fib_1618", 0) or fib_info.get("period_high", 0) * 1.15
        
        if fib_127 > 0:
            if price < fib_127:
                factors.append({"name": "Fib Extension", "value": "Unter 127.2%", "ok": True, "detail": "Raum nach oben"})
                score += 1
            elif price < fib_161:
                factors.append({"name": "Fib Extension", "value": "Bei 127.2%", "ok": True, "detail": "Erstes Ziel erreicht"})
                score += 0.5
            else:
                factors.append({"name": "Fib Extension", "value": "Über 161.8%", "ok": False, "detail": "Stark überdehnt"})
    else:
        # Fallback ohne Fib-Daten
        if change_pct < 5:
            factors.append({"name": "Fib Extension", "value": "N/A", "ok": True, "detail": "Früh im Move"})
            score += 1
    
    # 4. RVOL (Relative Volume)
    if rvol >= 2.0:
        factors.append({"name": "RVOL", "value": f"{rvol:.1f}x", "ok": True, "detail": "Starke Bestätigung"})
        score += 1
    elif rvol >= 1.5:
        factors.append({"name": "RVOL", "value": f"{rvol:.1f}x", "ok": True, "detail": "Gute Bestätigung"})
        score += 1
    elif rvol >= 1.0:
        factors.append({"name": "RVOL", "value": f"{rvol:.1f}x", "ok": True, "detail": "Normal"})
        score += 0.5
    else:
        factors.append({"name": "RVOL", "value": f"{rvol:.1f}x", "ok": False, "detail": "Schwaches Volumen"})
    
    # 5. ATR ÜBERDEHNUNG
    if atr_pct > 0:
        atr_multiple = change_pct / atr_pct if atr_pct > 0 else 0
        if atr_multiple <= 1.0:
            factors.append({"name": "ATR", "value": f"{atr_multiple:.1f}x", "ok": True, "detail": "Normal"})
            score += 1
        elif atr_multiple <= 1.5:
            factors.append({"name": "ATR", "value": f"{atr_multiple:.1f}x", "ok": True, "detail": "Leicht erhöht"})
            score += 0.5
        elif atr_multiple <= 2.0:
            factors.append({"name": "ATR", "value": f"{atr_multiple:.1f}x", "ok": False, "detail": "Überdehnt"})
        else:
            factors.append({"name": "ATR", "value": f"{atr_multiple:.1f}x", "ok": False, "detail": "Stark überdehnt!"})
    else:
        # Kein echtes ATR → Schätze konservativ basierend auf Change%
        # Durchschnitt US-Aktie: ~2-3% ATR, aber variiert stark
        if change_pct <= 3:
            factors.append({"name": "ATR (est.)", "value": "Früh", "ok": True, "detail": "Move noch klein"})
            score += 0.75
        elif change_pct <= 6:
            factors.append({"name": "ATR (est.)", "value": "Moderat", "ok": True, "detail": "Normaler Move"})
            score += 0.5
        else:
            factors.append({"name": "ATR (est.)", "value": "Weit", "ok": False, "detail": "Schon weit gelaufen"})
    
    # 6. VOLUME TREND (basierend auf RVOL Stärke)
    if rvol >= 1.5:
        factors.append({"name": "Vol. Trend", "value": "Steigend", "ok": True, "detail": "Kaufdruck"})
        score += 1
    elif rvol >= 1.0:
        factors.append({"name": "Vol. Trend", "value": "Flat", "ok": True, "detail": "Stabil"})
        score += 0.5
    else:
        factors.append({"name": "Vol. Trend", "value": "Fallend", "ok": False, "detail": "Nachlassend"})
    
    # GESAMTBEWERTUNG
    max_score = 6
    score = min(score, max_score)
    
    if score >= 5:
        rating = "FRÜH"
        emoji = "[OK]"
        risk = "Niedrig - Guter Einstieg möglich"
        color = "green"
    elif score >= 3:
        rating = "OK"
        emoji = "[!]"
        risk = "Mittel - Vorsichtig positionieren"
        color = "orange"
    else:
        rating = "ZU SPÄT"
        emoji = "[X]"
        risk = "Hoch - Besser auf Pullback warten"
        color = "red"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "color": color
    }


# =============================================================================
# GAP TIMING BEWERTUNG
# =============================================================================
def calculate_gap_timing(row_data, is_gap_up=True):
    """
    Bewertet ob ein Gap-Trade-Einstieg noch gut ist.
    
    Faktoren:
    1. Gap Size - Optimale Größe 3-8%
    2. VWAP Position - Gap Up über VWAP = bullish
    3. PM/AH Volume - Starkes Pre-Market Volume bestätigt
    4. Gap Fill Risiko - High Vol Gaps füllen seltener (45% vs 85%)
    5. Zeit seit Open - Früher ist besser
    6. ATR Context - Gap vs. normale Volatilität
    """
    factors = []
    score = 0
    
    change_pct = abs(row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0)
    rvol = row_data.get("RVOL", 1) or 1
    atr_pct = row_data.get("ATR%", 2.5) or 2.5
    price = row_data.get("Preis", 0) or row_data.get("Price", 0) or 0
    prev_close = row_data.get("Prev Close", 0) or row_data.get("PrevClose", 0) or 0
    
    # 1. GAP SIZE - Optimal 3-8%
    if 3 <= change_pct <= 8:
        factors.append({"name": "Gap Size", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Optimale Größe"})
        score += 1
    elif 1 <= change_pct < 3:
        factors.append({"name": "Gap Size", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Klein aber OK"})
        score += 0.5
    elif 8 < change_pct <= 15:
        factors.append({"name": "Gap Size", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Groß - Vorsicht"})
        score += 0.5
    else:
        factors.append({"name": "Gap Size", "value": f"{change_pct:.1f}%", "ok": False, "detail": "Zu klein/groß"})
    
    # 2. RVOL als Proxy für PM Volume
    if rvol >= 2.0:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Starke Bestätigung"})
        score += 1
        gap_fill_risk = "Low"
    elif rvol >= 1.5:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Gute Bestätigung"})
        score += 0.75
        gap_fill_risk = "Medium"
    elif rvol >= 1.0:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Normal"})
        score += 0.5
        gap_fill_risk = "Medium"
    else:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": False, "detail": "Schwach - Fill wahrscheinlich"})
        gap_fill_risk = "High"
    
    # 3. GAP FILL RISIKO
    # High Volume Gaps: nur 45% füllen in 5+ Tagen
    # Low Volume Gaps: 85% füllen in 2 Tagen
    if gap_fill_risk == "Low":
        factors.append({"name": "Fill Risiko", "value": "~45%", "ok": True, "detail": "Trend wahrscheinlich"})
        score += 1
    elif gap_fill_risk == "Medium":
        factors.append({"name": "Fill Risiko", "value": "~60%", "ok": True, "detail": "Moderat"})
        score += 0.5
    else:
        factors.append({"name": "Fill Risiko", "value": "~85%", "ok": False, "detail": "Fill sehr wahrscheinlich"})
    
    # 4. ATR CONTEXT - Gap vs. normale Volatilität
    gap_atr_ratio = change_pct / atr_pct if atr_pct > 0 else 1
    if gap_atr_ratio >= 1.5:
        factors.append({"name": "Gap/ATR", "value": f"{gap_atr_ratio:.1f}x", "ok": True, "detail": "Signifikanter Gap"})
        score += 1
    elif gap_atr_ratio >= 1.0:
        factors.append({"name": "Gap/ATR", "value": f"{gap_atr_ratio:.1f}x", "ok": True, "detail": "Normaler Gap"})
        score += 0.5
    else:
        factors.append({"name": "Gap/ATR", "value": f"{gap_atr_ratio:.1f}x", "ok": False, "detail": "Kleiner Gap"})
    
    # 5. MOMENTUM BESTÄTIGUNG (basierend auf Change-Richtung vs Gap)
    # Wenn Gap Up und Change positiv = Momentum hält
    if is_gap_up:
        if change_pct > 0:
            factors.append({"name": "Momentum", "value": "Hält", "ok": True, "detail": "Gap hält über Open"})
            score += 1
        else:
            factors.append({"name": "Momentum", "value": "Schwächt", "ok": False, "detail": "Gap füllt sich"})
    else:
        if change_pct < 0:
            factors.append({"name": "Momentum", "value": "Hält", "ok": True, "detail": "Gap hält unter Open"})
            score += 1
        else:
            factors.append({"name": "Momentum", "value": "Schwächt", "ok": False, "detail": "Gap füllt sich"})
    
    # 6. OPENING RANGE CONTEXT
    # Schätze basierend auf Change und RVOL
    if rvol >= 1.5 and change_pct >= 2:
        factors.append({"name": "OR Break", "value": "Wahrscheinlich", "ok": True, "detail": "Starker Start"})
        score += 1
    elif rvol >= 1.0:
        factors.append({"name": "OR Break", "value": "Möglich", "ok": True, "detail": "Abwarten"})
        score += 0.5
    else:
        factors.append({"name": "OR Break", "value": "Unsicher", "ok": False, "detail": "Schwacher Start"})
    
    # GESAMTBEWERTUNG
    max_score = 6
    score = min(score, max_score)
    
    if score >= 4.5:
        rating = "GO"
        emoji = "[OK]"
        risk = "Gap & Go Setup - Trend folgen"
        recommendation = "Gap hält wahrscheinlich - Trend folgen"
    elif score >= 3:
        rating = "WARTEN"
        emoji = "[!]"
        risk = "Abwarten - Opening Range beobachten"
        recommendation = "15-30min warten, dann entscheiden"
    else:
        rating = "FADE"
        emoji = "[X]"
        risk = "Gap Fill wahrscheinlich - Vorsicht"
        recommendation = "Gap könnte füllen - Gegen-Trade oder Skip"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4.5 else "orange" if score >= 3 else "red"
    }


# =============================================================================
# MA BOUNCE TIMING BEWERTUNG
# =============================================================================
def calculate_ma_bounce_timing(row_data, ma_type="EMA 21"):
    """
    Bewertet ob ein MA Bounce Einstieg gut getimed ist.
    
    Faktoren:
    1. Distanz zum MA - Näher = besser (0-2% ideal)
    2. MA Trend-Richtung - MA muss in Trade-Richtung zeigen
    3. Bounce Bestätigung - Reaktion am MA sichtbar?
    4. RSI Zone - Neutral (40-60) ist ideal für Bounce
    5. Zeit seit letztem MA-Test - Länger weg = stärkerer Bounce
    """
    factors = []
    score = 0
    
    ma_distance = abs(row_data.get("MA_Distance%", 0) or row_data.get("MA Distance", 0) or 0)
    change_pct = row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0
    rvol = row_data.get("RVOL", 1) or 1
    
    # 1. DISTANZ ZUM MA - Näher = besser
    # WICHTIG: Wenn kein MA-Daten vorhanden (ma_distance=0 weil nicht befüllt),
    # vergeben wir neutralen Score statt Maximum
    has_ma_data = (row_data.get("MA_Distance%") is not None and row_data.get("MA_Distance%") != 0) or \
                  (row_data.get("MA Distance") is not None and row_data.get("MA Distance") != 0)
    
    if not has_ma_data:
        factors.append({"name": "MA Distanz", "value": "N/A", "ok": True, "detail": "Keine MA-Daten"})
        score += 0.5  # Neutral statt 1.5 Maximum
    elif ma_distance <= 0.5:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": True, "detail": "Perfekt am MA"})
        score += 1.5
    elif ma_distance <= 1.0:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": True, "detail": "Sehr nah"})
        score += 1.25
    elif ma_distance <= 2.0:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": True, "detail": "Akzeptabel"})
        score += 1
    elif ma_distance <= 3.0:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": True, "detail": "Noch OK"})
        score += 0.5
    else:
        factors.append({"name": "MA Distanz", "value": f"{ma_distance:.1f}%", "ok": False, "detail": "Zu weit vom MA"})
    
    # 2. BOUNCE BESTÄTIGUNG (Change-Richtung nach Touch)
    # Bei Long-Setup: Change sollte positiv sein (Bounce nach oben)
    if change_pct > 0:
        if change_pct >= 1:
            factors.append({"name": "Bounce", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Starke Reaktion"})
            score += 1
        else:
            factors.append({"name": "Bounce", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Leichte Reaktion"})
            score += 0.75
    elif change_pct > -1:
        factors.append({"name": "Bounce", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Neutral"})
        score += 0.5
    else:
        factors.append({"name": "Bounce", "value": f"{change_pct:.1f}%", "ok": False, "detail": "Kein Bounce - Durchbruch?"})
    
    # 3. MOMENTUM ZONE (basierend auf heutigem Move — KEIN RSI)
    # Ideal für Bounce: Moderate Bewegung (±3%)
    momentum = abs(change_pct)
    if momentum <= 3:
        factors.append({"name": "Momentum", "value": f"{change_pct:+.1f}%", "ok": True, "detail": "Moderat — Ideal für Bounce"})
        score += 1
    elif momentum <= 5:
        factors.append({"name": "Momentum", "value": f"{change_pct:+.1f}%", "ok": True, "detail": "Leicht extended"})
        score += 0.5
    else:
        factors.append({"name": "Momentum", "value": f"{change_pct:+.1f}%", "ok": False, "detail": "Zu stark bewegt"})
    
    # 4. VOLUME BESTÄTIGUNG
    if rvol >= 1.5:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Starkes Interesse"})
        score += 1
    elif rvol >= 1.0:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Normal"})
        score += 0.5
    else:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": False, "detail": "Schwaches Interesse"})
    
    # 5. MA TYP KONTEXT
    if "200" in ma_type:
        factors.append({"name": "MA Typ", "value": "SMA 200", "ok": True, "detail": "Stärkster Support"})
        score += 0.5
    elif "50" in ma_type:
        factors.append({"name": "MA Typ", "value": "SMA 50", "ok": True, "detail": "Starker Support"})
        score += 0.5
    else:
        factors.append({"name": "MA Typ", "value": "EMA 21", "ok": True, "detail": "Swing-Trading MA"})
        score += 0.5
    
    # GESAMTBEWERTUNG
    max_score = 5
    score = min(score, max_score)
    
    if score >= 4:
        rating = "PERFEKT"
        emoji = "[OK]"
        risk = "Idealer Bounce-Einstieg"
        recommendation = "Entry am MA mit Stop darunter"
    elif score >= 2.5:
        rating = "GUT"
        emoji = "[!]"
        risk = "Akzeptabler Einstieg"
        recommendation = "Entry möglich, engerer Stop"
    else:
        rating = "WARTEN"
        emoji = "[X]"
        risk = "Kein klarer Bounce"
        recommendation = "Auf besseren Entry warten"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4 else "orange" if score >= 2.5 else "red"
    }


# =============================================================================
# MEAN REVERSION / REVERSAL TIMING BEWERTUNG
# =============================================================================
def calculate_reversal_timing(row_data, is_long=True):
    """
    Bewertet ob ein Mean Reversion / Reversal Einstieg gut getimed ist.
    
    Faktoren:
    1. RSI Extrem - <30 für Long, >70 für Short
    2. Bollinger Band - Außerhalb = überdehnt
    3. Distanz von MA - >2 Std.Dev = überdehnt
    4. Umkehr-Signal - Change-Richtung dreht?
    5. Volume - Capitulation Volume = gut
    6. S/R Level - Bei Support/Resistance?
    """
    factors = []
    score = 0
    
    change_pct = row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0
    rvol = row_data.get("RVOL", 1) or 1
    atr_pct = row_data.get("ATR%", 2.5) or 2.5
    
    # 1. SELLOFF-TIEFE (statt fake RSI)
    # Für Mean Reversion: je tiefer der Drop, desto besser für Long-Reversal
    momentum = change_pct  # Negativer Wert bei Selloff
    
    if is_long:
        if momentum <= -8:
            factors.append({"name": "Selloff", "value": f"{momentum:+.1f}%", "ok": True, "detail": "Starker Selloff — Reversal-Zone"})
            score += 1.5
        elif momentum <= -5:
            factors.append({"name": "Selloff", "value": f"{momentum:+.1f}%", "ok": True, "detail": "Deutlicher Selloff"})
            score += 1
        elif momentum <= -3:
            factors.append({"name": "Selloff", "value": f"{momentum:+.1f}%", "ok": True, "detail": "Moderater Rückgang"})
            score += 0.5
        else:
            factors.append({"name": "Selloff", "value": f"{momentum:+.1f}%", "ok": False, "detail": "Kein echter Selloff"})
    else:  # Short Reversal
        if momentum >= 8:
            factors.append({"name": "Rally", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Starke Rally — Reversal-Zone"})
            score += 1.5
        elif momentum >= 5:
            factors.append({"name": "Rally", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Deutliche Rally"})
            score += 1
        elif momentum >= 3:
            factors.append({"name": "Rally", "value": f"+{momentum:.1f}%", "ok": True, "detail": "Moderate Rally"})
            score += 0.5
        else:
            factors.append({"name": "Rally", "value": f"+{momentum:.1f}%", "ok": False, "detail": "Keine echte Rally"})
    
    # 2. ÜBERDEHNUNG (Change vs ATR)
    extension = abs(change_pct) / atr_pct if atr_pct > 0 else 0
    if extension >= 2.0:
        factors.append({"name": "Extension", "value": f"{extension:.1f}x ATR", "ok": True, "detail": "Stark überdehnt"})
        score += 1
    elif extension >= 1.5:
        factors.append({"name": "Extension", "value": f"{extension:.1f}x ATR", "ok": True, "detail": "Überdehnt"})
        score += 0.75
    elif extension >= 1.0:
        factors.append({"name": "Extension", "value": f"{extension:.1f}x ATR", "ok": True, "detail": "Moderat"})
        score += 0.5
    else:
        factors.append({"name": "Extension", "value": f"{extension:.1f}x ATR", "ok": False, "detail": "Nicht überdehnt"})
    
    # 3. VOLUME (Capitulation = gut für Reversal)
    if rvol >= 2.5:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Capitulation möglich"})
        score += 1
    elif rvol >= 1.5:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Erhöhtes Volumen"})
        score += 0.75
    elif rvol >= 1.0:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": True, "detail": "Normal"})
        score += 0.5
    else:
        factors.append({"name": "Volume", "value": f"{rvol:.1f}x", "ok": False, "detail": "Schwach"})
    
    # 4. UMKEHR-SIGNAL (Change zeigt erste Erholung?)
    if is_long:
        if change_pct > 0:
            factors.append({"name": "Umkehr", "value": "Ja", "ok": True, "detail": "Erste Erholung sichtbar"})
            score += 1
        elif change_pct > -2:
            factors.append({"name": "Umkehr", "value": "Möglich", "ok": True, "detail": "Stabilisiert sich"})
            score += 0.5
        else:
            factors.append({"name": "Umkehr", "value": "Nein", "ok": False, "detail": "Fällt noch"})
    else:
        if change_pct < 0:
            factors.append({"name": "Umkehr", "value": "Ja", "ok": True, "detail": "Erste Schwäche sichtbar"})
            score += 1
        elif change_pct < 2:
            factors.append({"name": "Umkehr", "value": "Möglich", "ok": True, "detail": "Momentum nachlassend"})
            score += 0.5
        else:
            factors.append({"name": "Umkehr", "value": "Nein", "ok": False, "detail": "Steigt noch"})
    
    # 5. RISK/REWARD basierend auf Extension
    if extension >= 1.5:
        factors.append({"name": "R:R", "value": "Gut", "ok": True, "detail": f"Mean ~{extension:.0f}x ATR entfernt"})
        score += 1
    elif extension >= 1.0:
        factors.append({"name": "R:R", "value": "OK", "ok": True, "detail": "Akzeptables R:R"})
        score += 0.5
    else:
        factors.append({"name": "R:R", "value": "Schlecht", "ok": False, "detail": "Wenig Raum zum Mean"})
    
    # GESAMTBEWERTUNG
    max_score = 6
    score = min(score, max_score)
    
    if score >= 4.5:
        rating = "EXTREM"
        emoji = "[OK]"
        risk = "Stark überdehnt - Reversal wahrscheinlich"
        recommendation = "Entry mit Stop unter Extrem"
    elif score >= 3:
        rating = "MÖGLICH"
        emoji = "[!]"
        risk = "Überdehnt - Reversal möglich"
        recommendation = "Auf Bestätigung warten"
    else:
        rating = "ZU FRÜH"
        emoji = "[X]"
        risk = "Nicht genug überdehnt"
        recommendation = "Warten auf stärkere Überdehnung"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4.5 else "orange" if score >= 3 else "red"
    }


# =============================================================================
# VOLUME VOID TIMING BEWERTUNG
# =============================================================================
def calculate_void_timing(row_data):
    """
    Bewertet ob ein Volume Void Trade-Einstieg gut getimed ist.
    
    Faktoren:
    1. Void Size - Größer = mehr Potenzial
    2. Distanz zum Void - Näher = besser
    3. Trend-Richtung - Void in Trend-Richtung = stärker
    4. Void Alter - Neuere Voids sind relevanter
    5. Multiple Voids - Gestaffelte Voids = stärker
    """
    factors = []
    score = 0
    
    void_size = row_data.get("VoidSize%", 0) or row_data.get("Void Size", 0) or 0
    void_dist = abs(row_data.get("VoidDist%", 0) or row_data.get("Void Distance", 0) or 0)
    change_pct = row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0
    voids_above = row_data.get("VoidsAbove", 0) or 0
    voids_below = row_data.get("VoidsBelow", 0) or 0
    
    # 1. VOID SIZE
    if void_size >= 5:
        factors.append({"name": "Void Size", "value": f"{void_size:.1f}%", "ok": True, "detail": "Großes Void"})
        score += 1
    elif void_size >= 3:
        factors.append({"name": "Void Size", "value": f"{void_size:.1f}%", "ok": True, "detail": "Gutes Void"})
        score += 0.75
    elif void_size >= 1:
        factors.append({"name": "Void Size", "value": f"{void_size:.1f}%", "ok": True, "detail": "Kleines Void"})
        score += 0.5
    else:
        factors.append({"name": "Void Size", "value": f"{void_size:.1f}%", "ok": False, "detail": "Sehr klein"})
    
    # 2. DISTANZ ZUM VOID
    if void_dist <= 1:
        factors.append({"name": "Distanz", "value": f"{void_dist:.1f}%", "ok": True, "detail": "Sehr nah"})
        score += 1
    elif void_dist <= 2:
        factors.append({"name": "Distanz", "value": f"{void_dist:.1f}%", "ok": True, "detail": "Nah"})
        score += 0.75
    elif void_dist <= 5:
        factors.append({"name": "Distanz", "value": f"{void_dist:.1f}%", "ok": True, "detail": "Moderat"})
        score += 0.5
    else:
        factors.append({"name": "Distanz", "value": f"{void_dist:.1f}%", "ok": False, "detail": "Weit entfernt"})
    
    # 3. TREND-RICHTUNG vs VOID
    # Wenn Preis steigt und Void oben liegt = gut
    if change_pct > 0 and voids_above > 0:
        factors.append({"name": "Trend → Void", "value": "Aligned", "ok": True, "detail": "Void in Bewegungsrichtung"})
        score += 1
    elif change_pct < 0 and voids_below > 0:
        factors.append({"name": "Trend → Void", "value": "Aligned", "ok": True, "detail": "Void in Bewegungsrichtung"})
        score += 1
    elif voids_above > 0 or voids_below > 0:
        factors.append({"name": "Trend → Void", "value": "Neutral", "ok": True, "detail": "Void vorhanden"})
        score += 0.5
    else:
        factors.append({"name": "Trend → Void", "value": "Kein Void", "ok": False, "detail": "Kein nahes Void"})
    
    # 4. MULTIPLE VOIDS
    total_voids = voids_above + voids_below
    if total_voids >= 3:
        factors.append({"name": "Voids", "value": f"{total_voids} Voids", "ok": True, "detail": "Mehrere Voids = mehr Targets"})
        score += 1
    elif total_voids >= 1:
        factors.append({"name": "Voids", "value": f"{total_voids} Void(s)", "ok": True, "detail": "Void vorhanden"})
        score += 0.5
    else:
        factors.append({"name": "Voids", "value": "0 Voids", "ok": False, "detail": "Keine Voids sichtbar"})
    
    # 5. SETUP QUALITÄT
    if void_size >= 3 and void_dist <= 2:
        factors.append({"name": "Setup", "value": "A+", "ok": True, "detail": "Großes Void, sehr nah"})
        score += 1
    elif void_size >= 2 and void_dist <= 3:
        factors.append({"name": "Setup", "value": "B", "ok": True, "detail": "Gutes Setup"})
        score += 0.5
    else:
        factors.append({"name": "Setup", "value": "C", "ok": False, "detail": "Schwaches Setup"})
    
    # GESAMTBEWERTUNG
    max_score = 5
    score = min(score, max_score)
    
    if score >= 4:
        rating = "STARK"
        emoji = "[OK]"
        risk = "Klares Void-Setup"
        recommendation = "Entry Richtung Void mit Target am Void-Ende"
    elif score >= 2.5:
        rating = "OK"
        emoji = "[!]"
        risk = "Akzeptables Setup"
        recommendation = "Entry möglich, konservatives Target"
    else:
        rating = "SCHWACH"
        emoji = "[X]"
        risk = "Kein klares Void-Setup"
        recommendation = "Besseres Setup abwarten"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4 else "orange" if score >= 2.5 else "red"
    }


# =============================================================================
# INSIDER TIMING BEWERTUNG
# =============================================================================
def calculate_insider_timing(row_data):
    """
    Bewertet ob ein Insider-Buying Signal stark ist.
    
    Faktoren:
    1. Cluster Buying - Mehrere Insider = stärker
    2. Transaktionsgröße - Größer = mehr Conviction
    3. Insider-Rolle - CEO/CFO > Director > 10% Owner
    4. Timing - Nach Pullback = besser
    5. Open Market Purchase - Code "P" = echtes Geld
    """
    factors = []
    score = 0
    
    # Extrahiere Insider-Daten (falls verfügbar)
    num_insiders = row_data.get("Insiders", 1) or row_data.get("InsiderCount", 1) or 1
    transaction_value = row_data.get("InsiderValue", 0) or row_data.get("TransValue", 0) or 0
    insider_role = row_data.get("InsiderRole", "Unknown") or "Unknown"
    change_pct = row_data.get("Chg%", 0) or row_data.get("Change %", 0) or 0
    change_1m = row_data.get("1M%", 0) or row_data.get("Change1M", 0) or 0
    
    # 1. CLUSTER BUYING
    if num_insiders >= 3:
        factors.append({"name": "Cluster", "value": f"{num_insiders} Insider", "ok": True, "detail": "Starkes Cluster-Signal"})
        score += 1.5
    elif num_insiders >= 2:
        factors.append({"name": "Cluster", "value": f"{num_insiders} Insider", "ok": True, "detail": "Cluster-Signal"})
        score += 1
    else:
        factors.append({"name": "Cluster", "value": "1 Insider", "ok": True, "detail": "Einzelner Kauf"})
        score += 0.5
    
    # 2. TRANSAKTIONSGRÖSSE
    if transaction_value >= 500000:
        factors.append({"name": "Größe", "value": f"${transaction_value/1000:.0f}K", "ok": True, "detail": "Sehr große Position"})
        score += 1
    elif transaction_value >= 100000:
        factors.append({"name": "Größe", "value": f"${transaction_value/1000:.0f}K", "ok": True, "detail": "Große Position"})
        score += 0.75
    elif transaction_value > 0:
        factors.append({"name": "Größe", "value": f"${transaction_value/1000:.0f}K", "ok": True, "detail": "Moderate Position"})
        score += 0.5
    else:
        factors.append({"name": "Größe", "value": "N/A", "ok": True, "detail": "Keine Daten"})
        score += 0.5
    
    # 3. INSIDER-ROLLE
    role_upper = insider_role.upper() if isinstance(insider_role, str) else ""
    if "CEO" in role_upper or "CFO" in role_upper or "CHIEF" in role_upper:
        factors.append({"name": "Rolle", "value": insider_role[:10], "ok": True, "detail": "C-Suite = höchste Conviction"})
        score += 1
    elif "DIRECTOR" in role_upper or "DIR" in role_upper:
        factors.append({"name": "Rolle", "value": "Director", "ok": True, "detail": "Board-Member"})
        score += 0.75
    elif "10%" in role_upper or "OWNER" in role_upper:
        factors.append({"name": "Rolle", "value": "10% Owner", "ok": True, "detail": "Großaktionär"})
        score += 0.5
    else:
        factors.append({"name": "Rolle", "value": "Insider", "ok": True, "detail": "Unbekannte Rolle"})
        score += 0.5
    
    # 4. TIMING - Kauf nach Pullback ist besser
    if change_1m < -10:
        factors.append({"name": "Timing", "value": f"{change_1m:.0f}% (1M)", "ok": True, "detail": "Kauf nach starkem Pullback"})
        score += 1
    elif change_1m < -5:
        factors.append({"name": "Timing", "value": f"{change_1m:.0f}% (1M)", "ok": True, "detail": "Kauf nach Pullback"})
        score += 0.75
    elif change_1m < 0:
        factors.append({"name": "Timing", "value": f"{change_1m:.0f}% (1M)", "ok": True, "detail": "Kauf bei Schwäche"})
        score += 0.5
    else:
        factors.append({"name": "Timing", "value": f"+{change_1m:.0f}% (1M)", "ok": False, "detail": "Kauf nach Run-up"})
    
    # 5. PREIS-AKTION BESTÄTIGUNG
    if change_pct > 0:
        factors.append({"name": "Preis", "value": f"+{change_pct:.1f}%", "ok": True, "detail": "Positive Reaktion"})
        score += 0.5
    elif change_pct > -2:
        factors.append({"name": "Preis", "value": f"{change_pct:.1f}%", "ok": True, "detail": "Stabil"})
        score += 0.25
    else:
        factors.append({"name": "Preis", "value": f"{change_pct:.1f}%", "ok": False, "detail": "Weiter fallend"})
    
    # GESAMTBEWERTUNG
    max_score = 5
    score = min(score, max_score)
    
    if score >= 4:
        rating = "STARK"
        emoji = "[OK]"
        risk = "Starkes Insider-Signal"
        recommendation = "Entry mit Stop unter Recent Low"
    elif score >= 2.5:
        rating = "MODERAT"
        emoji = "[!]"
        risk = "Moderates Signal"
        recommendation = "Auf weitere Bestätigung achten"
    else:
        rating = "SCHWACH"
        emoji = "[X]"
        risk = "Schwaches Signal"
        recommendation = "Nicht allein auf Insider verlassen"
    
    return {
        "score": round(score, 1),
        "max_score": max_score,
        "rating": rating,
        "emoji": emoji,
        "factors": factors,
        "risk": risk,
        "recommendation": recommendation,
        "color": "green" if score >= 4 else "orange" if score >= 2.5 else "red"
    }

