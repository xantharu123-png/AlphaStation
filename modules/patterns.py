"""
Pattern Recognition Module — Extrahiert aus scanner.py (V69.6)

Enthält:
- Flag/Candle Patterns (Validate, Analyze, Detect)
- Breakout Prediction (20-Signal Composite)
- Fibonacci/Harmonic (Gartley, Butterfly, Bat, Crab, Shark)
- Wyckoff Accumulation/Distribution
- SMC: Volume Imbalances, Order Blocks, Liquidity Levels
- Wolfe Waves, Chart Patterns (Double Top/Bottom, H&S, etc.)
"""
import math
import time
import requests
from modules.indicators import (
    calculate_sma, calculate_ema, calculate_rsi_from_bars,
    calculate_adx, calculate_atr_14, calculate_obv,
    calculate_macd, calculate_stochastic, calculate_ema_series,
    calculate_vwap, calculate_atr_from_ohlc
)
from modules.data_fetchers import rate_limited_get
from modules.volume_analysis import calculate_volume_profile, find_volume_voids



# ── validate_flag_pattern (originally line 1994) ──
def validate_flag_pattern(vortag_chg, change_today, rvol, price, prev_close, high, low, pattern_type="bull", prev_high=0, prev_low=0, market_type="Aktien", market_cap=None):
    """
    Validiert Bull/Bear Flag Pattern mit zusätzlichen Kriterien:

    Bull Flag Kriterien:
    1. Vortag: Starker Anstieg (Fahnenstange)
    2. Heute: Seitwärts/leicht runter (Konsolidierung)
    3. Volumen sinkt (RVOL < 1.5)
    4. Retracement < 50% der Fahnenstange
    
    LIMITATION: Snapshot API hat nur 1 Tag History.
    Echte Multi-Day Flags (3-5 Tage Fahnenstange) brauchen den History Scanner.
    Hier wird nur die gestrige OHLC-Kerze als Flagpole verwendet.
    
    Returns: (is_valid, score, details)
    """
    details = []
    score = 0
    is_crypto = market_type == "Krypto"
    
    if pattern_type == "bull":
        # Kriterium 1: Vorheriger Aufwärtstrend
        # V69.1 AUDIT FIX: Market-Cap-abhängige Schwellen für Crypto.
        # MegaCap (BTC/ETH) bewegt sich weniger als SmallCap Altcoins.
        if is_crypto:
            mc = market_cap or 0
            if mc > 100_000_000_000:      # Mega Cap (BTC, ETH)
                strong_thresh, mod_thresh = 0.5, 0.2
            elif mc > 10_000_000_000:     # Large Cap (SOL, BNB)
                strong_thresh, mod_thresh = 0.8, 0.3
            elif mc > 1_000_000_000:      # Mid Cap
                strong_thresh, mod_thresh = 1.5, 0.6
            else:                          # Small/Micro Cap
                strong_thresh, mod_thresh = 2.5, 1.0
            if vortag_chg >= strong_thresh:
                score += 25
                details.append(f" Starker 6d-Trend: {vortag_chg:+.1f}%/Tag (≈{vortag_chg*6:+.0f}%/Wo)")
            elif vortag_chg >= mod_thresh:
                score += 15
                details.append(f" Moderater 6d-Trend: {vortag_chg:+.1f}%/Tag")
            else:
                details.append(f" Kein Aufwärtstrend: {vortag_chg:+.1f}%/Tag avg")
        else:
            if 4.0 <= vortag_chg <= 30.0:
                score += 25
                details.append(f" Starke Fahnenstange: {vortag_chg:+.1f}%")
            elif 2.5 <= vortag_chg < 4.0:
                score += 15
                details.append(f" Moderate Fahnenstange: {vortag_chg:+.1f}%")
            else:
                details.append(f" Fahnenstange schwach: {vortag_chg:+.1f}%")

        # Kriterium 2: Konsolidierung
        consol_tight = 3.0 if is_crypto else 2.0
        consol_wide = 5.0 if is_crypto else 4.0
        if -consol_tight <= change_today <= consol_tight:
            score += 20
            details.append(f" Konsolidierung: {change_today:+.1f}%")
        elif -consol_wide <= change_today <= consol_wide:
            score += 10
            details.append(f" Leichte Konsolidierung: {change_today:+.1f}%")
        else:
            details.append(f" Keine Konsolidierung: {change_today:+.1f}%")

        # Kriterium 3: Volumen sinkt (Konsolidierung = weniger Aktivitaet)
        if rvol <= 0.8:
            score += 25
            details.append(f" Volumen sinkt stark: RVOL {rvol:.1f}x")
        elif rvol <= 1.2:
            score += 15
            details.append(f" Volumen sinkt: RVOL {rvol:.1f}x")
        elif rvol <= 1.8:
            score += 8
            details.append(f" Volumen leicht erhoeht: RVOL {rvol:.1f}x")
        else:
            details.append(f" Volumen zu hoch: RVOL {rvol:.1f}x")
        
        # Kriterium 4: Fibonacci Retracement Check
        # Fahnenstange = gestrige OHLC Kerze (High - Low), nicht nur Body
        # Das ist genauer als vortag_chg weil es die volle Range nutzt
        flagpole = 0
        if prev_high > 0 and prev_low > 0:
            flagpole = prev_high - prev_low  # Volle Kerzenrange als Flagpole

        if flagpole <= 0 and prev_close > 0 and vortag_chg > 0:
            # Fallback: Berechne aus vortag_chg wenn prev_high/Low fehlen
            _denom = 1 + vortag_chg / 100
            price_before_move = prev_close / _denom if abs(_denom) > 0.001 else prev_close
            flagpole = abs(prev_close - price_before_move)
        
        if flagpole > 0 and prev_high > 0:
            # Retracement = wie weit ist Preis vom gestrigen High gefallen?
            retracement = prev_high - low if low > 0 else 0
            retracement_pct = (retracement / flagpole * 100) if flagpole > 0 else 0
            
            if retracement_pct <= 38.2:
                score += 30
                details.append(f" Flaches Retracement: {retracement_pct:.1f}% (ideal)")
            elif retracement_pct <= 50.0:
                score += 20
                details.append(f" Gesundes Retracement: {retracement_pct:.1f}%")
            elif retracement_pct <= 61.8:
                score += 10
                details.append(f" Tiefes Retracement: {retracement_pct:.1f}%")
            else:
                details.append(f" Zu tiefes Retracement: {retracement_pct:.1f}%")
        
        # V69: Threshold von 50 auf 40 gesenkt — moderate Flags sollen auch angezeigt
        # werden, FlagScore zeigt die Qualität (je höher desto besser)
        is_valid = score >= 40

    else:  # Bear Flag
        # V69.1 AUDIT FIX: Market-Cap-abhängige Schwellen (analog Bull Flag)
        if is_crypto:
            mc = market_cap or 0
            if mc > 100_000_000_000:
                strong_thresh, mod_thresh = -0.5, -0.2
            elif mc > 10_000_000_000:
                strong_thresh, mod_thresh = -1.0, -0.4
            elif mc > 1_000_000_000:
                strong_thresh, mod_thresh = -1.5, -0.6
            else:
                strong_thresh, mod_thresh = -2.5, -1.0
            if vortag_chg <= strong_thresh:
                score += 25
                details.append(f" Starker Abwärtstrend: {vortag_chg:+.1f}%/Tag (≈{vortag_chg*6:+.0f}%/Wo)")
            elif vortag_chg <= mod_thresh:
                score += 15
                details.append(f" Moderater Abwärtstrend: {vortag_chg:+.1f}%/Tag")
            else:
                details.append(f" Kein Abwärtstrend: {vortag_chg:+.1f}%/Tag avg")
        else:
            if -30.0 <= vortag_chg < -4.0:
                score += 25
                details.append(f" Starke Fahnenstange (Short): {vortag_chg:+.1f}%")
            elif -4.0 <= vortag_chg <= -2.5:
                score += 15
                details.append(f" Moderate Fahnenstange: {vortag_chg:+.1f}%")
            else:
                details.append(f" Fahnenstange schwach: {vortag_chg:+.1f}%")

        consol_tight = 3.0 if is_crypto else 2.0
        consol_wide = 5.0 if is_crypto else 4.0
        if -consol_tight <= change_today <= consol_tight:
            score += 20
            details.append(f" Konsolidierung: {change_today:+.1f}%")
        elif -consol_wide <= change_today <= consol_wide:
            score += 10
            details.append(f" Leichte Konsolidierung: {change_today:+.1f}%")
        else:
            details.append(f" Keine Konsolidierung: {change_today:+.1f}%")

        if rvol <= 0.8:
            score += 25
            details.append(f" Volumen sinkt stark: RVOL {rvol:.1f}x")
        elif rvol <= 1.2:
            score += 15
            details.append(f" Volumen sinkt: RVOL {rvol:.1f}x")
        elif rvol <= 1.8:
            score += 8
            details.append(f" Volumen leicht erhoeht: RVOL {rvol:.1f}x")
        else:
            details.append(f" Volumen zu hoch: RVOL {rvol:.1f}x")
        
        # Bear Flag Retracement: Bounce von gestern Low zu heute High
        flagpole = 0
        if prev_high > 0 and prev_low > 0:
            flagpole = prev_high - prev_low

        if flagpole <= 0 and prev_close > 0 and vortag_chg < 0:
            _denom = 1 + vortag_chg / 100
            price_before_move = prev_close / _denom if abs(_denom) > 0.001 else prev_close
            flagpole = abs(price_before_move - prev_close)
        
        if flagpole > 0 and prev_low > 0:
            retracement = high - prev_low if high > 0 else 0
            retracement_pct = (retracement / flagpole * 100) if flagpole > 0 else 0
            
            if retracement_pct <= 38.2:
                score += 30
                details.append(f" Flacher Bounce: {retracement_pct:.1f}% (ideal)")
            elif retracement_pct <= 50.0:
                score += 20
                details.append(f" Gesunder Bounce: {retracement_pct:.1f}%")
            elif retracement_pct <= 61.8:
                score += 10
                details.append(f" Starker Bounce: {retracement_pct:.1f}%")
            else:
                details.append(f" Zu starker Bounce: {retracement_pct:.1f}%")
        
        # V69: Threshold von 50 auf 40 gesenkt (analog Bull Flag)
        is_valid = score >= 40

    return is_valid, score, details


# ── analyze_candles (originally line 2280) ──
def analyze_candles(candles):
    """
    Universelle Candlestick-Analyse auf einer Liste von OHLCV-Bars.

    Returns: dict mit allen technischen Signalen:
    - trend: "up" / "down" / "sideways"
    - trend_strength: 0-100
    - sma5, sma10, sma20: Simple Moving Averages
    - support, resistance: Key Levels
    - patterns: Liste erkannter Candlestick-Patterns
    - volume_trend: "accumulation" / "distribution" / "neutral"
    - consolidation: True/False + Dauer in Tagen
    - breakout_ready: True/False (enge Range + steigendes Volumen)
    - atr: Average True Range (14 Perioden)
    - higher_highs, higher_lows: Trend-Struktur
    """
    if not candles or len(candles) < 5:
        return {"trend": "unknown", "trend_strength": 0, "patterns": [],
                "support": 0, "resistance": 0, "consolidation": False,
                "breakout_ready": False, "volume_trend": "neutral",
                "atr": 0, "sma5": 0, "sma10": 0, "sma20": 0,
                "higher_highs": 0, "higher_lows": 0, "candle_count": 0}

    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    opens = [c["o"] for c in candles]
    volumes = [c.get("v", 0) for c in candles]
    n = len(candles)

    # =====================================================
    # 1. MOVING AVERAGES
    # =====================================================
    def sma(data, period):
        if len(data) < period:
            return data[-1] if data else 0
        return sum(data[-period:]) / period

    sma5 = sma(closes, 5)
    sma10 = sma(closes, 10)
    sma20 = sma(closes, 20)

    # =====================================================
    # 2. TREND ANALYSIS (Higher Highs / Higher Lows)
    # =====================================================
    hh_count = 0  # Higher Highs
    hl_count = 0  # Higher Lows
    lh_count = 0  # Lower Highs
    ll_count = 0  # Lower Lows
    lookback = min(10, n - 1)
    for i in range(n - lookback, n):
        if i <= 0:
            continue
        if highs[i] > highs[i - 1]:
            hh_count += 1
        else:
            lh_count += 1
        if lows[i] > lows[i - 1]:
            hl_count += 1
        else:
            ll_count += 1

    if hh_count >= lookback * 0.6 and hl_count >= lookback * 0.6:
        trend = "up"
        trend_strength = min(100, int((hh_count + hl_count) / max(1, lookback * 2) * 100))
    elif lh_count >= lookback * 0.6 and ll_count >= lookback * 0.6:
        trend = "down"
        trend_strength = min(100, int((lh_count + ll_count) / max(1, lookback * 2) * 100))
    else:
        trend = "sideways"
        trend_strength = 30

    # SMA-Bestätigung
    if closes[-1] > sma5 > sma10 > sma20:
        if trend == "up":
            trend_strength = min(100, trend_strength + 20)
        elif trend != "up":
            trend = "up"
            trend_strength = 50
    elif closes[-1] < sma5 < sma10 < sma20:
        if trend == "down":
            trend_strength = min(100, trend_strength + 20)
        elif trend != "down":
            trend = "down"
            trend_strength = 50

    # =====================================================
    # 3. SUPPORT / RESISTANCE
    # =====================================================
    recent_lows = sorted(lows[-15:])[:3] if n >= 15 else sorted(lows)[:3]
    recent_highs = sorted(highs[-15:], reverse=True)[:3] if n >= 15 else sorted(highs, reverse=True)[:3]
    support = sum(recent_lows) / len(recent_lows) if recent_lows else 0
    resistance = sum(recent_highs) / len(recent_highs) if recent_highs else 0

    # =====================================================
    # 4. ATR (Average True Range, 14 Perioden)
    # =====================================================
    true_ranges = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        true_ranges.append(tr)
    atr = sum(true_ranges[-14:]) / min(14, len(true_ranges)) if true_ranges else 0

    # =====================================================
    # 5. VOLUME TREND (Accumulation / Distribution)
    # =====================================================
    if n >= 10:
        recent_vol = sum(volumes[-5:]) / 5
        prior_vol = sum(volumes[-10:-5]) / 5 if sum(volumes[-10:-5]) > 0 else 1
        vol_ratio = recent_vol / prior_vol if prior_vol > 0 else 1.0

        # Prüfe ob Volumen mit Preis steigt (Accumulation) oder dagegen (Distribution)
        recent_price_up = closes[-1] > closes[-5]
        if vol_ratio > 1.2 and recent_price_up:
            volume_trend = "accumulation"
        elif vol_ratio > 1.2 and not recent_price_up:
            volume_trend = "distribution"
        else:
            volume_trend = "neutral"
    else:
        volume_trend = "neutral"
        vol_ratio = 1.0

    # =====================================================
    # 6. CONSOLIDATION DETECTION
    # =====================================================
    # Prüfe die letzten 5-10 Kerzen auf enge Range
    consol_bars = min(10, n)
    consol_highs = highs[-consol_bars:]
    consol_lows = lows[-consol_bars:]
    consol_range = (max(consol_highs) - min(consol_lows))
    consol_range_pct = (consol_range / closes[-1] * 100) if closes[-1] > 0 else 100

    is_consolidating = consol_range_pct < 8.0  # < 8% Range in 10 Tagen

    # Wie viele Tage konsolidiert? (zähle von hinten)
    consol_days = 0
    if is_consolidating:
        _mid = (max(consol_highs) + min(consol_lows)) / 2
        _band = consol_range * 0.6
        for i in range(n - 1, max(0, n - 20), -1):
            if abs(closes[i] - _mid) <= _band:
                consol_days += 1
            else:
                break

    # =====================================================
    # 7. BREAKOUT-BEREITSCHAFT
    # =====================================================
    # Enge Konsolidierung + steigendes Volumen = Breakout imminent
    breakout_ready = False
    if is_consolidating and consol_days >= 3:
        if n >= 5:
            last3_vol = sum(volumes[-3:]) / 3
            prior3_vol = sum(volumes[-6:-3]) / 3 if n >= 6 else last3_vol
            if prior3_vol > 0 and last3_vol / prior3_vol > 1.3:
                breakout_ready = True

    # =====================================================
    # 8. CANDLESTICK PATTERN RECOGNITION
    # =====================================================
    patterns = []

    if n >= 2:
        # Letzte 3 Kerzen analysieren
        for idx in range(max(0, n - 3), n):
            o, h, l, c = opens[idx], highs[idx], lows[idx], closes[idx]
            body = abs(c - o)
            full_range = h - l if h > l else 0.0001
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l
            body_pct = body / full_range if full_range > 0 else 0
            is_last = (idx == n - 1)
            pos_label = "heute" if is_last else f"vor {n - 1 - idx}d"

            # Doji (kleiner Body, grosse Wicks)
            if body_pct < 0.15 and full_range > 0:
                patterns.append({"name": "Doji", "type": "neutral", "pos": pos_label,
                                 "signal": "Unentschlossenheit — mögliche Trendwende"})

            # Langer unterer Docht — Trend-Kontext bestimmt ob Hammer oder Hanging Man
            # Hammer (nach Downtrend) = bullisch | Hanging Man (nach Uptrend) = bearisch
            elif body > full_range * 0.05 and lower_wick > body * 2 and upper_wick < body * 0.5:
                if trend == "down":
                    patterns.append({"name": "Hammer", "type": "bullish", "pos": pos_label,
                                     "signal": "Kaufdruck am Tief im Abwärtstrend — bullisches Reversal"})
                elif trend == "up":
                    patterns.append({"name": "Hanging Man", "type": "bearish", "pos": pos_label,
                                     "signal": "Warnsignal im Aufwärtstrend — möglicher Top"})
                else:
                    patterns.append({"name": "Hammer", "type": "neutral", "pos": pos_label,
                                     "signal": "Langer unterer Docht — Kontext unklar (Seitwärts)"})

            # Langer oberer Docht — Trend-Kontext bestimmt ob Inv. Hammer oder Shooting Star
            # Inverted Hammer (nach Downtrend) = bullisch | Shooting Star (nach Uptrend) = bearisch
            elif body > full_range * 0.05 and upper_wick > body * 2 and lower_wick < body * 0.5:
                if trend == "up":
                    patterns.append({"name": "Shooting Star", "type": "bearish", "pos": pos_label,
                                     "signal": "Verkaufsdruck am Hoch im Aufwärtstrend — bearisches Reversal"})
                elif trend == "down":
                    patterns.append({"name": "Inverted Hammer", "type": "bullish", "pos": pos_label,
                                     "signal": "Käufer testen höhere Preise im Abwärtstrend"})
                else:
                    patterns.append({"name": "Shooting Star", "type": "neutral", "pos": pos_label,
                                     "signal": "Langer oberer Docht — Kontext unklar (Seitwärts)"})

            # Marubozu (starker Body, keine Wicks)
            elif body_pct > 0.85:
                if c > o:
                    patterns.append({"name": "Bullish Marubozu", "type": "bullish", "pos": pos_label,
                                     "signal": "Starker Kaufdruck — Käufer dominieren"})
                else:
                    patterns.append({"name": "Bearish Marubozu", "type": "bearish", "pos": pos_label,
                                     "signal": "Starker Verkaufsdruck — Verkäufer dominieren"})

        # Multi-Candle Patterns (letzte 2 Kerzen)
        if n >= 2:
            o1, h1, l1, c1 = opens[-2], highs[-2], lows[-2], closes[-2]
            o2, h2, l2, c2 = opens[-1], highs[-1], lows[-1], closes[-1]

            # Bullish Engulfing
            if c1 < o1 and c2 > o2 and o2 <= c1 and c2 >= o1:
                patterns.append({"name": "Bullish Engulfing", "type": "bullish", "pos": "heute",
                                 "signal": "Käufer übernehmen — starkes Reversal-Signal"})

            # Bearish Engulfing
            elif c1 > o1 and c2 < o2 and o2 >= c1 and c2 <= o1:
                patterns.append({"name": "Bearish Engulfing", "type": "bearish", "pos": "heute",
                                 "signal": "Verkäufer übernehmen — starkes Abwärtssignal"})

            # Piercing Line (bullish)
            elif c1 < o1 and c2 > o2 and o2 < l1 and c2 > (o1 + c1) / 2:
                patterns.append({"name": "Piercing Line", "type": "bullish", "pos": "heute",
                                 "signal": "Gap Down + Recovery über Mittelpunkt — bullisch"})

            # Dark Cloud Cover (bearish)
            elif c1 > o1 and c2 < o2 and o2 > h1 and c2 < (o1 + c1) / 2:
                patterns.append({"name": "Dark Cloud Cover", "type": "bearish", "pos": "heute",
                                 "signal": "Gap Up + Abverkauf unter Mittelpunkt — bearisch"})

        # 3-Candle Patterns
        if n >= 3:
            o1, c1 = opens[-3], closes[-3]
            o2, c2, h2, l2 = opens[-2], closes[-2], highs[-2], lows[-2]
            o3, c3 = opens[-1], closes[-1]
            body2 = abs(c2 - o2)
            range2 = h2 - l2 if h2 > l2 else 0.0001

            # Morning Star (bullish reversal)
            if c1 < o1 and body2 / range2 < 0.3 and c3 > o3 and c3 > (o1 + c1) / 2:
                patterns.append({"name": "Morning Star", "type": "bullish", "pos": "heute",
                                 "signal": "3-Kerzen Reversal — Boden gefunden"})

            # Evening Star (bearish reversal)
            elif c1 > o1 and body2 / range2 < 0.3 and c3 < o3 and c3 < (o1 + c1) / 2:
                patterns.append({"name": "Evening Star", "type": "bearish", "pos": "heute",
                                 "signal": "3-Kerzen Reversal — Top erreicht"})

            # Three White Soldiers — 3 grüne Kerzen, jede öffnet innerhalb des vorherigen Body
            if (closes[-3] > opens[-3] and closes[-2] > opens[-2] and closes[-1] > opens[-1]
                    and closes[-1] > closes[-2] > closes[-3]
                    and opens[-2] >= opens[-3] and opens[-2] <= closes[-3]  # Öffnet in vorherigem Body
                    and opens[-1] >= opens[-2] and opens[-1] <= closes[-2]):
                patterns.append({"name": "Three White Soldiers", "type": "bullish", "pos": "heute",
                                 "signal": "3 steigende grüne Kerzen ohne Gaps — starker Aufwärtsdruck"})

            # Three Black Crows — 3 rote Kerzen, jede öffnet innerhalb des vorherigen Body
            if (closes[-3] < opens[-3] and closes[-2] < opens[-2] and closes[-1] < opens[-1]
                    and closes[-1] < closes[-2] < closes[-3]
                    and opens[-2] <= opens[-3] and opens[-2] >= closes[-3]  # Öffnet in vorherigem Body
                    and opens[-1] <= opens[-2] and opens[-1] >= closes[-2]):
                patterns.append({"name": "Three Black Crows", "type": "bearish", "pos": "heute",
                                 "signal": "3 fallende rote Kerzen ohne Gaps — starker Abwärtsdruck"})

    return {
        "trend": trend,
        "trend_strength": trend_strength,
        "sma5": round(sma5, 4),
        "sma10": round(sma10, 4),
        "sma20": round(sma20, 4),
        "support": round(support, 4),
        "resistance": round(resistance, 4),
        "atr": round(atr, 4),
        "patterns": patterns,
        "volume_trend": volume_trend,
        "consolidation": is_consolidating,
        "consol_days": consol_days,
        "consol_range_pct": round(consol_range_pct, 2),
        "breakout_ready": breakout_ready,
        "higher_highs": hh_count,
        "higher_lows": hl_count,
        "candle_count": n,
    }


# ── detect_flag_pattern_multiday (originally line 2588) ──
def detect_flag_pattern_multiday(poly_key, ticker, pattern_type="bull"):
    """
    Echte Multi-Day Bull/Bear Flag Erkennung mit Daily Candlesticks.

    Bull Flag:
    1. POLE: 2-7 Tage starker Anstieg (mind. +5% total)
    2. FLAG: 2-7 Tage Konsolidierung (enge Range, sinkendes Volumen)
    3. Retracement der Flag < 50% der Pole
    4. Flag-Volumen < Pole-Volumen (Selling pressure nimmt ab)

    Returns: (is_valid, score, details, flag_data)
    """
    from datetime import datetime as _dt, timedelta as _td

    details = []
    score = 0
    flag_data = {}

    try:
        end_date = _dt.utcnow().date()
        start_date = end_date - _td(days=30)  # 30 Tage für genug Kontext

        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        resp = rate_limited_get(url, params={"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": 30}, timeout=5)

        if resp.status_code != 200:
            return False, 0, [" Keine History-Daten verfügbar"], {}

        bars = resp.json().get("results", [])
        if len(bars) < 8:
            return False, 0, [" Zu wenig Kerzen für Flag-Analyse"], {}

        # Letzte 20 Kerzen verwenden
        candles = bars[-20:] if len(bars) >= 20 else bars
        n = len(candles)

        # Berechne tägliche Returns und Volumen
        closes = [c["c"] for c in candles]
        highs = [c["h"] for c in candles]
        lows = [c["l"] for c in candles]
        opens = [c["o"] for c in candles]
        volumes = [c.get("v", 0) for c in candles]

        is_bull = (pattern_type == "bull")

        # =====================================================
        # SCHRITT 1: Pole finden (starke Bewegung)
        # =====================================================
        # Suche den besten Pole in den letzten 15 Kerzen
        # Pole = aufeinanderfolgende Kerzen mit starkem Trend
        best_pole = None
        best_pole_score = 0

        for pole_start in range(max(0, n - 15), n - 4):  # Mind. 4 Kerzen nach Pole-Start
            for pole_len in range(2, min(8, n - pole_start - 1)):  # Pole: 2-7 Kerzen
                pole_end = pole_start + pole_len
                if pole_end >= n - 1:
                    break

                pole_move_pct = (closes[pole_end] - closes[pole_start]) / closes[pole_start] * 100

                # Bull: mind. +5% | Bear: mind. -5%
                if is_bull and pole_move_pct < 5.0:
                    continue
                if not is_bull and pole_move_pct > -5.0:
                    continue

                # Prüfe ob Pole konsistent ist (mind. 60% der Kerzen in Trendrichtung)
                trend_candles = 0
                for i in range(pole_start, pole_end):
                    daily_move = closes[i + 1] - closes[i]
                    if (is_bull and daily_move > 0) or (not is_bull and daily_move < 0):
                        trend_candles += 1
                consistency = trend_candles / pole_len
                if consistency < 0.5:
                    continue

                # Pole-Volumen (Durchschnitt)
                pole_vol = sum(volumes[pole_start:pole_end + 1]) / (pole_len + 1) if pole_len > 0 else 0

                # =====================================================
                # SCHRITT 2: Flag nach dem Pole suchen
                # =====================================================
                flag_start = pole_end
                remaining = n - flag_start  # Kerzen ab flag_start (inklusive)

                if remaining < 3:  # Mind. 3 Kerzen (flag_start + 2 weitere)
                    continue

                flag_len = min(remaining - 1, 7)  # Flag-Kerzen nach flag_start

                # Flag-Range: Wie eng konsolidiert der Preis?
                # Slice: [flag_start : flag_start + flag_len] — exakt flag_len Kerzen
                _flag_end = min(flag_start + flag_len, n)
                flag_highs = highs[flag_start:_flag_end]
                flag_lows = lows[flag_start:_flag_end]
                flag_closes = closes[flag_start:_flag_end]
                flag_vols = volumes[flag_start:_flag_end]

                flag_range = max(flag_highs) - min(flag_lows)
                # V69-FIX: Pole-Range = Pole High - Pole Low (nicht Closes!)
                _pole_high = max(highs[pole_start:pole_end + 1])
                _pole_low = min(lows[pole_start:pole_end + 1])
                pole_range = _pole_high - _pole_low

                if pole_range <= 0:
                    continue

                # Flag-Bewegung (wie viel hat sich der Preis in der Flag bewegt?)
                flag_move_pct = (flag_closes[-1] - flag_closes[0]) / flag_closes[0] * 100 if flag_closes[0] > 0 else 0

                # Flag soll seitwärts/leicht gegen Trend sein
                if is_bull and flag_move_pct > 3.0:
                    continue  # Flag steigt zu stark weiter → kein Flag
                if not is_bull and flag_move_pct < -3.0:
                    continue  # Flag fällt weiter → kein Flag

                # Retracement Check — gemessen vom Pole-Extremum
                if is_bull:
                    retracement = _pole_high - min(flag_lows)  # V69-FIX: Pole HIGH als Basis
                else:
                    retracement = max(flag_highs) - _pole_low  # V69-FIX: Pole LOW als Basis

                retracement_pct = (retracement / pole_range * 100) if pole_range > 0 else 100

                if retracement_pct > 62:
                    continue  # Zu tiefes Retracement → kein Flag

                # Flag-Volumen vs Pole-Volumen
                flag_vol_avg = sum(flag_vols) / len(flag_vols) if flag_vols else 0
                vol_decline = (flag_vol_avg / pole_vol) if pole_vol > 0 else 1.0

                # =====================================================
                # SCHRITT 3: Score berechnen
                # =====================================================
                _score = 0
                _details = []

                # A) Pole-Stärke (max 30)
                abs_move = abs(pole_move_pct)
                if abs_move >= 15:
                    _score += 30
                    _details.append(f" Starke Fahnenstange: {pole_move_pct:+.1f}% über {pole_len} Tage")
                elif abs_move >= 10:
                    _score += 25
                    _details.append(f" Gute Fahnenstange: {pole_move_pct:+.1f}% über {pole_len} Tage")
                elif abs_move >= 5:
                    _score += 18
                    _details.append(f" Moderate Fahnenstange: {pole_move_pct:+.1f}% über {pole_len} Tage")

                # B) Pole-Konsistenz (max 10)
                if consistency >= 0.8:
                    _score += 10
                    _details.append(f" Konsistenter Trend: {consistency*100:.0f}% der Kerzen")
                elif consistency >= 0.6:
                    _score += 5
                    _details.append(f" Moderater Trend: {consistency*100:.0f}% der Kerzen")

                # C) Flag-Enge (max 20)
                flag_tightness = (flag_range / pole_range * 100) if pole_range > 0 else 100
                if flag_tightness <= 30:
                    _score += 20
                    _details.append(f" Sehr enge Flag: {flag_tightness:.0f}% der Pole-Range")
                elif flag_tightness <= 50:
                    _score += 15
                    _details.append(f" Enge Flag: {flag_tightness:.0f}% der Pole-Range")
                elif flag_tightness <= 70:
                    _score += 8
                    _details.append(f" Breite Flag: {flag_tightness:.0f}% der Pole-Range")

                # D) Retracement (max 20)
                if retracement_pct <= 23.6:
                    _score += 20
                    _details.append(f" Minimales Retracement: {retracement_pct:.1f}%")
                elif retracement_pct <= 38.2:
                    _score += 15
                    _details.append(f" Flaches Retracement: {retracement_pct:.1f}%")
                elif retracement_pct <= 50.0:
                    _score += 10
                    _details.append(f" Gesundes Retracement: {retracement_pct:.1f}%")
                else:
                    _score += 3
                    _details.append(f" Tiefes Retracement: {retracement_pct:.1f}%")

                # E) Volumen-Decline in Flag (max 20)
                if vol_decline <= 0.5:
                    _score += 20
                    _details.append(f" Volumen stark gesunken: {vol_decline:.0%} des Pole-Vol")
                elif vol_decline <= 0.75:
                    _score += 15
                    _details.append(f" Volumen sinkt: {vol_decline:.0%} des Pole-Vol")
                elif vol_decline <= 1.0:
                    _score += 8
                    _details.append(f" Volumen stabil: {vol_decline:.0%} des Pole-Vol")
                else:
                    _details.append(f" Volumen steigt: {vol_decline:.0%} des Pole-Vol")

                # V69-FIX: Freshness — Flag muss die letzten Kerzen einschließen
                # _flag_end ist der Index nach dem letzten Flag-Element
                # Pattern ist nur relevant wenn Flag bis max 2 Kerzen vor Ende reicht
                if _flag_end < n - 2:
                    continue  # Stale Pattern — zu alt, kein aktuelles Signal

                # Bestes Flag-Pattern behalten
                if _score > best_pole_score:
                    best_pole_score = _score
                    best_pole = {
                        "pole_start": pole_start, "pole_end": pole_end,
                        "pole_move": pole_move_pct, "pole_len": pole_len,
                        "flag_len": flag_len, "retracement": retracement_pct,
                        "vol_decline": vol_decline, "flag_tightness": flag_tightness,
                        "score": _score, "details": _details,
                        "pole_high": max(highs[pole_start:pole_end + 1]),
                        "pole_low": min(lows[pole_start:pole_end + 1]),
                        "flag_high": max(flag_highs),
                        "flag_low": min(flag_lows),
                        "current_price": closes[-1],
                        # V69-FIX: Dollar-Formel für Target (Breakout + Pole-Höhe)
                        "target": max(flag_highs) + pole_range if is_bull
                                  else min(flag_lows) - pole_range,
                    }

        if best_pole is None:
            return False, 0, [" Kein Flag-Pattern in den letzten 20 Tagen gefunden"], {}

        score = best_pole["score"]
        details = best_pole["details"]

        # Target-Info
        if is_bull:
            details.append(f" Target: ${best_pole['target']:.2f} (Pole-Höhe auf Breakout)")
        else:
            details.append(f" Target: ${best_pole['target']:.2f} (Pole-Höhe auf Breakdown)")

        is_valid = score >= 55  # V69-FIX: Threshold erhöht (40→55) für weniger False Positives

        return is_valid, score, details, best_pole

    except Exception as e:
        return False, 0, [f" Flag-Analyse Fehler: {str(e)[:80]}"], {}


# ── analyze_breakout_imminent (originally line 3296) ──
def analyze_breakout_imminent(bars, direction="long", crypto_mode=False):
    """
     BREAKOUT IMMINENT V2.1 — 20-Signal Composite Prediction (Pro-Reweighted)

    Kombiniert 20 Faktoren um bevorstehende Long/Short Breakouts vorherzusagen.
    Maximum: 188 Punkte — GEWICHTET nach Trader-Wisdom:

    crypto_mode=True: Volume-Signale (2,3,8,16,19) werden durch Spread-basierte
    Preis-Proxies ersetzt, da CoinGecko kein historisches Volume liefert.

    BOOSTED (Smart Money / Momentum — diese Signale unterscheiden echte Breakouts):
      OBV Divergenz (13), ADX Turning (14), Inst. Accumulation (14),
      Relative Strength (14), Order Blocks (14), Liquidity Pools (14)

    CUT (Dead Stock Indicators — hohe Scores bei toten Aktien):
      ATR Squeeze (6), Vol Dry-Up (5), Range Duration (5),
      Tight Compression (6), Body Compression (5)

    NEUTRAL (unveraendert bei 10):
      Close Clustering, Boundary Tests, RSI Drift, Higher Lows,
      MACD Histogram, Stochastic, FVG Proximity, Fib Confluence,
      Volume Profile Void

    Basiert auf: Minervini SEPA/VCP, O'Neil CANSLIM, Turtle Traders,
    Weinstein Stage Analysis, Van Tharp Position Sizing, Wyckoff

    Args:
        bars: Liste von OHLC-Dicts (min 15 Tage, ideal 30)
        direction: "long" oder "short"

    Returns:
        (is_valid, score, max_score, details, direction_confidence, grade)
    """
    if not bars or len(bars) < 15:
        return False, 0, 188, ["Nicht genug Daten (min 15 Tage)"], 0, "D", 0, 0

    score = 0
    sm_fires = 0  # Smart Money Fires (Boosted-Signale auf Maximum)
    sm_hits = 0    # + Smart Money Hits (Boosted-Signale aktiv)
    details = []
    n = len(bars)

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars]

    current_price = closes[-1]

    # ===================================================================
    # SIGNAL 1: VOLATILITAETS-KONTRAKTION (ATR Squeeze) — max 6 Punkte [CUT]
    # ATR schrumpft = Energie baut sich auf, ABER auch bei toten Aktien!
    # FIX 2: Filter out penny stocks with low liquidity (< 500K volume, < $5 price)
    # ===================================================================
    daily_ranges = []
    for b in bars:
        if b["close"] > 0:
            daily_ranges.append((b["high"] - b["low"]) / b["close"] * 100)

    avg_volume = sum(volumes) / len(volumes) if volumes else 0
    close = closes[-1] if closes else 0
    is_penny_illiquid = (avg_volume < 500000 and close < 5)
    # V3.2: Smart-Money-Signale brauchen Mindest-Liquidität
    # Bei <100K avg Volume ist OBV/ADX/Institutional Accumulation pures Rauschen
    # RVOL-Check NICHT hier — Volume Dry-Up (RVOL 0.7) ist das Wyckoff-Setup VOR Breakouts.
    # Stattdessen: sm_eligible nur über avg_volume. RVOL-Prüfung kommt beim Grading
    # (IHS wird über price_flat + kein Score trotzdem nicht Grade A)
    sm_eligible = avg_volume >= 100_000 or crypto_mode

    if len(daily_ranges) >= 15 and not is_penny_illiquid:
        # Vergleiche LETZTE 5 Tage vs VORHERIGE 15 Tage (sensitiver als Halbierung)
        recent_atr = sum(daily_ranges[-5:]) / 5
        prior_atr = sum(daily_ranges[-20:-5]) / max(1, len(daily_ranges[-20:-5]))

        if prior_atr > 0:
            atr_ratio = recent_atr / prior_atr
            if atr_ratio < 0.5:
                score += 6
                details.append(f" ATR-Squeeze extrem: {atr_ratio:.2f}x (Ranges halbiert)")
            elif atr_ratio < 0.7:
                score += 4
                details.append(f" ATR-Squeeze stark: {atr_ratio:.2f}x")
            elif atr_ratio < 0.85:
                score += 2
                details.append(f" ATR leicht sinkend: {atr_ratio:.2f}x")
            else:
                details.append(f" Kein ATR-Squeeze: {atr_ratio:.2f}x")
        else:
            details.append(" ATR-Squeeze: Keine Prior-ATR Daten")
    else:
        details.append(" ATR-Squeeze: Nicht genug Daten (min 15 Tage)")

    # ===================================================================
    # SIGNAL 2: VOLUME DRY-UP / BODY COMPRESSION — max 5 Punkte [CUT]
    # Crypto: Body-Kompression (|close-open|/range schrumpft) statt Spread Dry-Up
    # V69.1 FIX: Signal 1 (ATR Squeeze) misst bereits daily_ranges — hier
    # messen wir stattdessen ob die BODIES kleiner werden (Doji-artig),
    # was Unentschlossenheit = Energie-Aufbau signalisiert.
    # FIX 2: Filter out penny stocks with low liquidity (< 500K volume, < $5 price)
    # ===================================================================
    if not is_penny_illiquid and crypto_mode:
        # Body-Ratio: |close-open| / (high-low) pro Bar — 0=Doji, 1=Marubozu
        body_ratios = []
        for b in bars:
            bar_range = b["high"] - b["low"]
            if bar_range > 0:
                body_ratios.append(abs(b["close"] - b["open"]) / bar_range)
        if len(body_ratios) >= 15:
            recent_body = sum(body_ratios[-5:]) / 5
            prior_body = sum(body_ratios[-20:-5]) / max(1, len(body_ratios[-20:-5]))
            if prior_body > 0:
                body_decline = recent_body / prior_body
                if body_decline < 0.4:
                    score += 5
                    details.append(f" Body-Kompression extrem: {body_decline:.2f}x (Doji-Phase)")
                elif body_decline < 0.6:
                    score += 3
                    details.append(f" Body-Kompression stark: {body_decline:.2f}x")
                elif body_decline < 0.8:
                    score += 2
                    details.append(f" Body leicht schrumpfend: {body_decline:.2f}x")
                else:
                    details.append(f" Keine Body-Kompression: {body_decline:.2f}x")
            else:
                details.append(" Body-Kompression: Keine Prior-Daten")
        else:
            details.append(" Body-Kompression: Nicht genug Daten")
    elif not is_penny_illiquid:
        if len(volumes) >= 15:
            recent_vol = sum(volumes[-5:]) / 5
            prior_vol = sum(volumes[-20:-5]) / max(1, len(volumes[-20:-5]))

            if prior_vol > 0:
                vol_decline = recent_vol / prior_vol
                if vol_decline < 0.5:
                    score += 5
                    details.append(f" Vol Dry-Up extrem: {vol_decline:.2f}x")
                elif vol_decline < 0.7:
                    score += 3
                    details.append(f" Vol sinkt deutlich: {vol_decline:.2f}x")
                elif vol_decline < 0.85:
                    score += 2
                    details.append(f" Vol leicht sinkend: {vol_decline:.2f}x")
                else:
                    details.append(f" Kein Vol Dry-Up: {vol_decline:.2f}x")
            else:
                details.append(" Vol Dry-Up: Kein Prior-Volumen")
        else:
            details.append(" Vol Dry-Up: Nicht genug Daten (min 15 Tage)")
    else:
        details.append(" Vol Dry-Up: Penny/Illiquid Stock ignoriert")

    # ===================================================================
    # SIGNAL 3: OBV-DIVERGENZ / CLOSE-MOMENTUM DIVERGENZ — max 13 Punkte [BOOSTED]
    # Crypto: Cumulative Close Delta (wie OBV aber mit Preis-Änderung statt Volume)
    # ===================================================================
    price_change_pct = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] > 0 else 0
    price_flat = abs(price_change_pct) < 5  # Preis relativ flat

    if crypto_mode:
        # Cumulative Close-Delta: Summe der täglichen Preis-Änderungen (wie OBV ohne Vol)
        ccd = [0]
        for i in range(1, n):
            ccd.append(ccd[-1] + (closes[i] - closes[i-1]))
        if len(ccd) >= 6:
            # V68: CCD FLOW-Vergleich statt Level-Durchschnitte (gleicher Fix wie OBV)
            # CCD ist kumulativ → alte Level-Durchschnitte waren systematisch verzerrt.
            # Neue Logik: Vergleiche Netto-Preis-Momentum in jeder Hälfte.
            mid = len(ccd) // 2
            early_flow = ccd[mid] - ccd[0]      # Netto-Momentum erste Hälfte
            late_flow = ccd[-1] - ccd[mid]       # Netto-Momentum zweite Hälfte
            ccd_rising = late_flow > 0 and (early_flow <= 0 or late_flow > early_flow * 0.5)
            ccd_falling = late_flow < 0 and (early_flow >= 0 or late_flow < early_flow * 0.5)

            if price_flat:
                if direction == "long" and ccd_rising:
                    score += 13; sm_fires += 1; sm_hits += 1
                    details.append(f" Close-Momentum bullisch: Preis flat, Momentum steigt")
                elif direction == "short" and ccd_falling:
                    score += 13; sm_fires += 1; sm_hits += 1
                    details.append(f" Close-Momentum baerisch: Preis flat, Momentum faellt")
                elif direction == "long" and ccd_falling:
                    details.append(f" Close-Momentum faellt = eher Short")
                elif direction == "short" and ccd_rising:
                    details.append(f" Close-Momentum steigt = eher Long")
                else:
                    score += 4
                    details.append(f" Close-Momentum neutral")
            else:
                if direction == "long" and ccd_rising:
                    score += 7; sm_hits += 1
                    details.append(f" Close-Momentum steigt ({price_change_pct:+.1f}%)")
                elif direction == "short" and ccd_falling:
                    score += 7; sm_hits += 1
                    details.append(f" Close-Momentum faellt ({price_change_pct:+.1f}%)")
                else:
                    details.append(f" Close-Momentum passt nicht ({price_change_pct:+.1f}%)")
        else:
            details.append(" Close-Momentum: Nicht genug Daten")
    else:
        obv = [0]
        for i in range(1, n):
            if closes[i] > closes[i-1]:
                obv.append(obv[-1] + volumes[i])
            elif closes[i] < closes[i-1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])

        if len(obv) >= 6:
            # V68: OBV FLOW-Vergleich statt Level-Durchschnitte
            # ALTE LOGIK (FALSCH): Verglich Durchschnitte kumulativer OBV-Werte.
            # Problem: Bei kumulativen Daten ist die 2. Hälfte MECHANISCH höher,
            # auch wenn OBV gerade FÄLLT (Distribution). Eine Aktie mit OBV
            # [0→1000→0] zeigte "OBV Rising" weil avg(2.Hälfte) > avg(1.Hälfte).
            # NEUE LOGIK: Vergleiche den NETTO-FLOW (Zufluss/Abfluss) in jeder Hälfte.
            # early_flow = OBV-Änderung in 1. Hälfte, late_flow = OBV-Änderung in 2. Hälfte
            # So erkennt man ob Smart Money AKTUELL kauft/verkauft.
            mid = len(obv) // 2
            early_flow = obv[mid] - obv[0]      # Netto-Zufluss erste Hälfte
            late_flow = obv[-1] - obv[mid]       # Netto-Zufluss zweite Hälfte
            # Rising: Positiver Flow in 2. Hälfte UND stärker als 1. Hälfte (oder 1. war negativ)
            obv_rising = late_flow > 0 and (early_flow <= 0 or late_flow > early_flow * 0.5)
            # Falling: Negativer Flow in 2. Hälfte UND stärker als 1. Hälfte (oder 1. war positiv)
            obv_falling = late_flow < 0 and (early_flow >= 0 or late_flow < early_flow * 0.5)

            if price_flat:
                if direction == "long" and obv_rising:
                    score += 13; sm_fires += 1; sm_hits += 1
                    details.append(f" OBV-Divergenz bullisch: Preis flat, OBV steigt [Smart Money!]")
                elif direction == "short" and obv_falling:
                    score += 13; sm_fires += 1; sm_hits += 1
                    details.append(f" OBV-Divergenz baerisch: Preis flat, OBV faellt [Smart Money!]")
                elif direction == "long" and obv_falling:
                    details.append(f" OBV faellt = eher Short")
                elif direction == "short" and obv_rising:
                    details.append(f" OBV steigt = eher Long")
                else:
                    # AUDIT FIX: Neutral OBV = kein Signal, 0 Punkte (war +4 = Score-Inflation)
                    details.append(f" OBV neutral (kein Signal)")
            else:
                if direction == "long" and obv_rising:
                    score += 7; sm_hits += 1
                    details.append(f" OBV steigt (Preis nicht flat: {price_change_pct:+.1f}%)")
                elif direction == "short" and obv_falling:
                    score += 7; sm_hits += 1
                    details.append(f" OBV faellt (Preis nicht flat: {price_change_pct:+.1f}%)")
                else:
                    details.append(f" OBV-Trend passt nicht zur Richtung (Preis: {price_change_pct:+.1f}%)")
        else:
            details.append(" OBV: Nicht genug Daten")

    # ===================================================================
    # SIGNAL 4: CLOSE POSITION CLUSTERING — max 10 Punkte
    # Closes clustern nahe High (bullish) oder Low (bearish)
    # ===================================================================
    if n >= 5:
        recent_close_positions = []
        for b in bars[-5:]:
            rng = b["high"] - b["low"]
            if rng > 0:
                cp = (b["close"] - b["low"]) / rng
                recent_close_positions.append(cp)

        if recent_close_positions:
            avg_cp = sum(recent_close_positions) / len(recent_close_positions)

            if direction == "long" and avg_cp > 0.7:
                score += 10
                details.append(f" Closes clustern nahe Highs: {avg_cp:.0%}")
            elif direction == "long" and avg_cp > 0.55:
                score += 5
                details.append(f" Closes leicht bullisch: {avg_cp:.0%}")
            elif direction == "short" and avg_cp < 0.3:
                score += 10
                details.append(f" Closes clustern nahe Lows: {avg_cp:.0%}")
            elif direction == "short" and avg_cp < 0.45:
                score += 5
                details.append(f" Closes leicht baerisch: {avg_cp:.0%}")
            else:
                details.append(f" Close Position neutral: {avg_cp:.0%}")

    # ===================================================================
    # SIGNAL 5: RANGE DURATION — max 5 Punkte [CUT]
    # Laengere Konsolidierung, ABER zu lang = tote Aktie!
    # FIX 2: Filter out penny stocks with low liquidity (< 500K volume, < $5 price)
    # ===================================================================
    # Zaehle aufeinanderfolgende Tage in enger Range (vom Ende rueckwaerts)
    range_days = 0
    max_range_high = highs[-1]
    min_range_low = lows[-1]

    for i in range(n - 2, -1, -1):
        max_range_high = max(max_range_high, highs[i])
        min_range_low = min(min_range_low, lows[i])
        total_range = ((max_range_high - min_range_low) / current_price) * 100 if current_price > 0 else 99

        if total_range < 6:  # Range < 6% gilt als echte Konsolidierung
            range_days += 1
        else:
            break

    if not is_penny_illiquid:
        if range_days >= 15:
            score += 5
            details.append(f" Lange Konsolidierung: {range_days} Tage")
        elif range_days >= 10:
            score += 3
            details.append(f" Solide Konsolidierung: {range_days} Tage")
        elif range_days >= 6:
            score += 2
            details.append(f" Kurze Konsolidierung: {range_days} Tage")
        else:
            details.append(f" Keine Konsolidierung: {range_days} Tage")
    else:
        details.append(f" Range Duration: Penny/Illiquid Stock ignoriert ({range_days} Tage)")

    # ===================================================================
    # SIGNAL 6: RANGE BOUNDARY TESTS — max 10 Punkte
    # Mehrfache Tests der Grenze = Widerstand wird schwaecher (Wyckoff Logic)
    # FIX (BUG 1): 4+ boundary tests WITHOUT volume increase = EXHAUSTION (reduced score)
    # Multiple resistance tests + DECLINING volume = weak breakout (max 3pts)
    # Multiple resistance tests + INCREASING volume = accumulation (max 10pts)
    # ===================================================================
    if range_days >= 5:
        range_high = max(highs[-range_days:])
        range_low = min(lows[-range_days:])
        range_size = range_high - range_low

        if range_size > 0:
            threshold_upper = range_high - range_size * 0.10  # Top 10% der Range (strenger)
            threshold_lower = range_low + range_size * 0.10   # Bottom 10%

            upper_tests = sum(1 for h in highs[-range_days:] if h >= threshold_upper)
            lower_tests = sum(1 for l in lows[-range_days:] if l <= threshold_lower)

            # FIX (BUG 1): Check volume TREND over test period (accumulation vs exhaustion)
            # Compare avg volume of last 5 bars vs last 15 bars
            vol_last_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else sum(volumes) / len(volumes)
            vol_last_15 = sum(volumes[-15:]) / 15 if len(volumes) >= 15 else sum(volumes) / len(volumes)
            vol_trend_ratio = vol_last_5 / vol_last_15 if vol_last_15 > 0 else 0
            volume_increasing = vol_trend_ratio > 1.2  # Volume is rising (accumulation)

            if direction == "long" and upper_tests >= 4:
                if volume_increasing:
                    # Multiple tests + RISING volume = accumulation at resistance (full score)
                    score += 10
                    details.append(f" {upper_tests}x Resistance + STEIGENDE Volumen (Wyckoff Acc): {vol_trend_ratio:.2f}x")
                else:
                    # Multiple tests + DECLINING volume = exhaustion (reduced score)
                    score += 3
                    details.append(f" {upper_tests}x Resistance OHNE Volumen-Steigerung (Wyckoff Exhaust): {vol_trend_ratio:.2f}x")
            elif direction == "long" and upper_tests >= 3:
                if volume_increasing:
                    score += 5
                    details.append(f" {upper_tests}x Resistance + steigende Volumen: {vol_trend_ratio:.2f}x")
                else:
                    score += 2
                    details.append(f" {upper_tests}x Resistance ohne Volumen-Boost: {vol_trend_ratio:.2f}x")
            elif direction == "short" and lower_tests >= 4:
                if volume_increasing:
                    # Multiple tests + RISING volume = distribution at support (full score)
                    score += 10
                    details.append(f" {lower_tests}x Support + STEIGENDE Volumen (Wyckoff Dist): {vol_trend_ratio:.2f}x")
                else:
                    # Multiple tests + DECLINING volume = exhaustion (reduced score)
                    score += 3
                    details.append(f" {lower_tests}x Support OHNE Volumen-Steigerung (Wyckoff Exhaust): {vol_trend_ratio:.2f}x")
            elif direction == "short" and lower_tests >= 3:
                if volume_increasing:
                    score += 5
                    details.append(f" {lower_tests}x Support + steigende Volumen: {vol_trend_ratio:.2f}x")
                else:
                    score += 2
                    details.append(f" {lower_tests}x Support ohne Volumen-Boost: {vol_trend_ratio:.2f}x")
            else:
                details.append(f" Wenig Boundary-Tests (Upper: {upper_tests}, Lower: {lower_tests})")
        else:
            details.append(f" Boundary-Tests: Range-Size = 0")
    else:
        details.append(f" Boundary-Tests: Keine Konsolidierung ({range_days} Tage < 5)")

    # ===================================================================
    # SIGNAL 7: ADX TURNING UP — max 14 Punkte [BOOSTED]
    # ADX < 20 + steigend = neuer Trend beginnt JETZT (Minervini/O'Neil Key Signal)
    # ===================================================================
    adx, adx_prev = calculate_adx(bars)

    if adx is not None:
        if adx < 20 and adx_prev and adx > adx_prev:
            score += 14; sm_fires += 1; sm_hits += 1
            details.append(f" ADX Wende: {adx_prev:.0f}→{adx:.0f} (unter 20 + steigend = Breakout!)")
        elif adx < 25 and adx_prev and adx > adx_prev:
            score += 9; sm_hits += 1
            details.append(f" ADX steigend: {adx_prev:.0f}→{adx:.0f}")
        elif adx < 20:
            score += 4
            details.append(f" ADX niedrig ({adx:.0f}) aber nicht steigend")
        else:
            details.append(f" ADX bereits hoch: {adx:.0f} (Trend laeuft schon)")

    # ===================================================================
    # SIGNAL 8: INSTITUTIONAL ACCUMULATION / SPREAD-EXPANSION DAYS — max 7 Punkte [FIX 1: Reduced from 14 to 7]
    # Crypto: Spread-Expansion + Close Direction als Volume-Proxy
    # AUDIT: OBV Divergence (Signal 3) + RSI Drift (Signal 9) bereits messen Buying Pressure
    # Reduced to avoid triple-counting "buying pressure despite flat price"
    # ===================================================================
    if n >= 10:
        if crypto_mode:
            # Crypto: Tage mit überdurchschnittlichem Spread + Close in Richtung
            avg_spread = sum(daily_ranges) / len(daily_ranges) if daily_ranges else 1
            accum_days = 0
            distri_days = 0
            for i in range(1, n):
                bar_spread = (bars[i]["high"] - bars[i]["low"]) / bars[i]["close"] * 100 if bars[i]["close"] > 0 else 0
                if bar_spread > avg_spread * 1.3:  # Überdurchschnittliche Aktivität
                    if closes[i] > closes[i-1]:
                        accum_days += 1
                    elif closes[i] < closes[i-1]:
                        distri_days += 1
        else:
            avg_vol = sum(volumes) / n
            accum_days = 0
            distri_days = 0
            for i in range(1, n):
                if volumes[i] > avg_vol * 1.5:
                    if closes[i] > closes[i-1]:
                        accum_days += 1
                    elif closes[i] < closes[i-1]:
                        distri_days += 1

        if direction == "long" and accum_days >= 4 and accum_days > distri_days * 1.5:
            score += 7; sm_hits += 1; sm_fires += 1
            details.append(f" {'Spread' if crypto_mode else 'Inst.'}-Akkumulation: {accum_days} Akku vs {distri_days} Distri [SM]")
        elif direction == "long" and accum_days >= 3 and accum_days > distri_days:
            score += 4; sm_hits += 1
            details.append(f" Akkumulation: {accum_days} vs {distri_days} Tage")
        elif direction == "short" and distri_days >= 4 and distri_days > accum_days * 1.5:
            score += 7; sm_hits += 1; sm_fires += 1
            details.append(f" {'Spread' if crypto_mode else 'Inst.'}-Distribution: {distri_days} Distri vs {accum_days} Akku [SM]")
        elif direction == "short" and distri_days >= 3 and distri_days > accum_days:
            score += 4; sm_hits += 1
            details.append(f" Distribution: {distri_days} vs {accum_days} Tage")
        else:
            details.append(f" Gemischte Aktivitaet: {accum_days} Akku / {distri_days} Distri")

    # ===================================================================
    # SIGNAL 9: RSI DRIFT — max 5 Punkte [FIX 1: Reduced from 10 to 5]
    # RSI driftet ueber 55 (bullisch) oder unter 45 (baerisch) waehrend
    # Preis noch flat ist = Momentum baut sich unsichtbar auf
    # AUDIT: Overlaps with Stochastic (Signal 14) — avoid double-counting momentum
    # FIX 4: Score RSI but will take max(rsi_points, stoch_points) to avoid dedup
    # ===================================================================
    rsi = calculate_rsi_from_bars(bars)
    rsi_points = 0  # Will be used in FIX 4 dedup logic
    rsi_detail = ""

    if rsi is not None:
        if price_flat:
            # Preis flat + RSI driftet = unsichtbares Momentum (starkes Signal)
            if direction == "long" and 55 <= rsi <= 65:
                rsi_points = 5
                rsi_detail = f" RSI-Drift bullisch: {rsi:.0f} (Preis flat, Momentum baut auf)"
            elif direction == "long" and 50 <= rsi < 55:
                rsi_points = 2
                rsi_detail = f" RSI leicht bullisch: {rsi:.0f}"
            elif direction == "short" and 35 <= rsi <= 45:
                rsi_points = 5
                rsi_detail = f" RSI-Drift baerisch: {rsi:.0f} (Preis flat, Schwaeche baut auf)"
            elif direction == "short" and 45 < rsi <= 50:
                rsi_points = 2
                rsi_detail = f" RSI leicht baerisch: {rsi:.0f}"
            elif 40 <= rsi <= 60:
                rsi_points = 2
                rsi_detail = f" RSI neutral: {rsi:.0f}"
            else:
                rsi_detail = f" RSI extrem: {rsi:.0f}"
        else:
            # Preis nicht flat — RSI trotzdem bewerten aber weniger Punkte
            if direction == "long" and 50 <= rsi <= 65:
                rsi_points = 4
                rsi_detail = f" RSI bullisch: {rsi:.0f} (Preis bewegt: {price_change_pct:+.1f}%)"
            elif direction == "short" and 35 <= rsi <= 50:
                rsi_points = 4
                rsi_detail = f" RSI baerisch: {rsi:.0f} (Preis bewegt: {price_change_pct:+.1f}%)"
            else:
                rsi_detail = f" RSI: {rsi:.0f} (Preis nicht flat: {price_change_pct:+.1f}%)"
    else:
        rsi_detail = " RSI: Nicht genug Daten"

    # ===================================================================
    # SIGNAL 10: HIGHER LOWS / LOWER HIGHS IN RANGE — max 10 Punkte
    # Zeigt welche Seite die Kontrolle gewinnt
    # FIX (BUG 2): Random walk produces ~50% higher lows. Raise threshold from 65% to 75%.
    # 75% = full 10pts (statistically significant vs chance)
    # 65% = intermediate 5pts (only marginally above random)
    # ===================================================================
    if range_days >= 6:
        recent_lows = lows[-range_days:]
        recent_highs = highs[-range_days:]

        # Higher Lows Check (bullisch)
        higher_lows = 0
        for i in range(1, len(recent_lows)):
            if recent_lows[i] > recent_lows[i-1]:
                higher_lows += 1
        hl_pct = higher_lows / max(1, len(recent_lows) - 1)

        # Lower Highs Check (baerisch)
        lower_highs = 0
        for i in range(1, len(recent_highs)):
            if recent_highs[i] < recent_highs[i-1]:
                lower_highs += 1
        lh_pct = lower_highs / max(1, len(recent_highs) - 1)

        if direction == "long" and hl_pct >= 0.75:
            score += 10
            details.append(f" Higher Lows STARK: {hl_pct:.0%} der Tage = Bullen dominieren")
        elif direction == "long" and hl_pct >= 0.65:
            score += 5
            details.append(f" Higher Lows: {hl_pct:.0%} der Tage (marginal ueber Zufall)")
        elif direction == "long" and hl_pct >= 0.50:
            score += 2
            details.append(f" Tendenz Higher Lows: {hl_pct:.0%}")
        elif direction == "short" and lh_pct >= 0.75:
            score += 10
            details.append(f" Lower Highs STARK: {lh_pct:.0%} der Tage = Baeren dominieren")
        elif direction == "short" and lh_pct >= 0.65:
            score += 5
            details.append(f" Lower Highs: {lh_pct:.0%} der Tage (marginal ueber Zufall)")
        elif direction == "short" and lh_pct >= 0.50:
            score += 2
            details.append(f" Tendenz Lower Highs: {lh_pct:.0%}")
        else:
            details.append(f" Keine klare Struktur (HL: {hl_pct:.0%}, LH: {lh_pct:.0%})")
    else:
        details.append(f" Higher Lows/Lower Highs: Zu kurze Range ({range_days} Tage < 6)")

    # ===================================================================
    # SIGNAL 11: RELATIVE STAERKE vs MARKT — max 14 Punkte [BOOSTED]
    # Resilience = Minervini RS-Rating Proxy (Key SEPA Criterion)
    # ===================================================================
    # Statt SPY-Vergleich: Prüfe ob Aktie sich in Range haelt trotz Volatilitaet
    if n >= 10:
        negative_days = sum(1 for i in range(1, n) if closes[i] < closes[i-1])
        recovery_days = 0
        for i in range(2, n):
            if closes[i-1] < closes[i-2] and closes[i] > closes[i-1]:
                recovery_days += 1

        # V68: Resilience auf [0, 1] begrenzen — verhindert Inflation bei wenigen Down-Days
        # V3.2: 0 Down-Days = perfekte Resilience (1.0), nicht 0%
        # Eine Aktie die 30 Tage nur steigt ist maximal resilient
        if negative_days == 0:
            resilience = 1.0
        else:
            resilience = min(1.0, recovery_days / negative_days)

        if direction == "long" and resilience > 0.7:
            score += 14; sm_fires += 1; sm_hits += 1
            details.append(f" Hohe Resilience: {resilience:.0%} Recovery nach Dips [Minervini RS!]")
        elif direction == "long" and resilience > 0.5:
            score += 7; sm_hits += 1
            details.append(f" Gute Resilience: {resilience:.0%}")
        elif direction == "short" and resilience < 0.3:
            score += 14; sm_fires += 1; sm_hits += 1
            details.append(f" Schwache Resilience: {resilience:.0%} = Verkaufsdruck")
        elif direction == "short" and resilience < 0.5:
            score += 7; sm_hits += 1
            details.append(f" Maessige Resilience: {resilience:.0%}")
        else:
            details.append(f" Resilience neutral: {resilience:.0%}")

    # ===================================================================
    # SIGNAL 12: TIGHT RANGE COMPRESSION (Bollinger-Squeeze Proxy) — max 6 Punkte [CUT]
    # StdDev schrumpft, ABER extreme Kompression = oft tote Aktie!
    # FIX 2: Filter out penny stocks with low liquidity (< 500K volume, < $5 price)
    # ===================================================================
    if n >= 10 and not is_penny_illiquid:
        recent_closes = closes[-10:]
        mean_price = sum(recent_closes) / len(recent_closes)
        variance = sum((c - mean_price) ** 2 for c in recent_closes) / len(recent_closes)
        std_dev_pct = ((variance ** 0.5) / mean_price) * 100 if mean_price > 0 else 99

        if std_dev_pct < 1.5:
            score += 6
            details.append(f" Extreme Kompression: StdDev {std_dev_pct:.2f}%")
        elif std_dev_pct < 2.5:
            score += 4
            details.append(f" Starke Kompression: StdDev {std_dev_pct:.2f}%")
        elif std_dev_pct < 4.0:
            score += 2
            details.append(f" Moderate Kompression: StdDev {std_dev_pct:.2f}%")
        else:
            details.append(f" Keine Kompression: StdDev {std_dev_pct:.2f}%")

    # ===================================================================
    # SIGNAL 13: MACD HISTOGRAM DIVERGENZ — max 10 Punkte
    # MACD-Histogram dreht → unsichtbares Momentum baut auf
    # ===================================================================
    macd_line, signal_line, hist = calculate_macd(bars)
    if hist and len(hist) >= 3:
        # Histogram Slope: letzte 3 Werte
        hist_slope = hist[-1] - hist[-3]
        hist_turning = (hist[-2] < hist[-1]) if direction == "long" else (hist[-2] > hist[-1])

        if direction == "long" and hist[-1] < 0 and hist_slope > 0 and hist_turning:
            score += 10
            details.append(f" MACD-Divergenz bullisch: Histogram dreht auf ({hist[-1]:.3f})")
        elif direction == "long" and hist_slope > 0:
            score += 5
            details.append(f" MACD-Histogram steigend")
        elif direction == "short" and hist[-1] > 0 and hist_slope < 0 and hist_turning:
            score += 10
            details.append(f" MACD-Divergenz baerisch: Histogram kippt ({hist[-1]:.3f})")
        elif direction == "short" and hist_slope < 0:
            score += 5
            details.append(f" MACD-Histogram fallend")
        else:
            details.append(f" MACD neutral (Hist: {hist[-1]:.3f})")
    else:
        details.append(" MACD: Nicht genug Daten")

    # ===================================================================
    # SIGNAL 14: STOCHASTIC MOMENTUM — max 10 Punkte
    # %K/%D Kreuzung in Extremzonen = starkes Timing-Signal
    # FIX 4: Score Stochastic but will take max(rsi_points, stoch_points) to avoid dedup
    # ===================================================================
    stoch_k, stoch_d = calculate_stochastic(bars)
    stoch_points = 0  # Will be used in FIX 4 dedup logic
    stoch_detail = ""

    if stoch_k is not None and stoch_d is not None:
        if direction == "long":
            # Breakout-Kontext: %K rising + crossover ist wichtiger als Extremzone
            if stoch_k < 30 and stoch_k > stoch_d:
                stoch_points = 10
                stoch_detail = f" Stochastic bullisch: %K={stoch_k:.0f} kreuzt %D={stoch_d:.0f} in Oversold"
            elif stoch_k < 50 and stoch_k > stoch_d:
                stoch_points = 7
                stoch_detail = f" Stochastic steigend aus Mitte: %K={stoch_k:.0f} > %D={stoch_d:.0f}"
            elif stoch_k > stoch_d:
                stoch_points = 3
                stoch_detail = f" Stochastic steigend: %K={stoch_k:.0f} (aber schon hoch)"
            elif stoch_k > 80:
                stoch_detail = f" Stochastic ueberkauft: {stoch_k:.0f}"
            else:
                stoch_detail = f" Stochastic neutral: %K={stoch_k:.0f}"
        else:  # short
            if stoch_k > 70 and stoch_k < stoch_d:
                stoch_points = 10
                stoch_detail = f" Stochastic baerisch: %K={stoch_k:.0f} kreuzt %D={stoch_d:.0f} in Overbought"
            elif stoch_k > 50 and stoch_k < stoch_d:
                stoch_points = 7
                stoch_detail = f" Stochastic fallend aus Mitte: %K={stoch_k:.0f} < %D={stoch_d:.0f}"
            elif stoch_k < stoch_d:
                stoch_points = 3
                stoch_detail = f" Stochastic fallend: %K={stoch_k:.0f} (aber schon niedrig)"
            elif stoch_k < 20:
                stoch_detail = f" Stochastic ueberverkauft: {stoch_k:.0f}"
            else:
                stoch_detail = f" Stochastic neutral: %K={stoch_k:.0f}"
    else:
        stoch_detail = " Stochastic: Nicht genug Daten"

    # ===================================================================
    # SIGNAL 15: ORDER BLOCK CONFLUENCE — max 14 Punkte [BOOSTED]
    # Breakout nahe Order Block = institutionelle Zone (Wyckoff/ICT)
    # ===================================================================
    try:
        ob_data = detect_order_blocks(bars, max_blocks=5)
        range_high_15 = max(highs[-min(15, n):])
        range_low_15 = min(lows[-min(15, n):])
        atr_ob = sum((bars[i]["high"] - bars[i]["low"]) for i in range(max(0, n-10), n)) / min(10, n)

        if direction == "long":
            bull_obs = ob_data.get("bullish_obs", [])
            if bull_obs:
                # V68 Fix: Key "ob_high"/"ob_low" statt "zone_high"/"zone_low" (KeyError behoben)
                near_breakout = any(abs(ob["ob_high"] - range_high_15) < atr_ob * 2 for ob in bull_obs)
                near_support = any(abs(ob["ob_low"] - range_low_15) < atr_ob * 2 for ob in bull_obs)
                if near_breakout:
                    score += 14; sm_fires += 1; sm_hits += 1
                    details.append(f" Bullish OB nahe Breakout-Level = institutionelles Kaufinteresse!")
                elif near_support:
                    score += 9; sm_hits += 1
                    details.append(f" Bullish OB stuetzt Range-Low = Demand Zone")
                else:
                    score += 4
                    details.append(f" Bullish OBs vorhanden ({len(bull_obs)}x) aber nicht in Naehe")
            else:
                details.append(f" Keine Bullish Order Blocks")
        else:  # short
            bear_obs = ob_data.get("bearish_obs", [])
            if bear_obs:
                # V68 Fix: Key "ob_low"/"ob_high" statt "zone_low"/"zone_high"
                near_breakout = any(abs(ob["ob_low"] - range_low_15) < atr_ob * 2 for ob in bear_obs)
                near_resistance = any(abs(ob["ob_high"] - range_high_15) < atr_ob * 2 for ob in bear_obs)
                if near_breakout:
                    score += 14; sm_fires += 1; sm_hits += 1
                    details.append(f" Bearish OB nahe Breakdown-Level = institutioneller Verkaufsdruck!")
                elif near_resistance:
                    score += 9; sm_hits += 1
                    details.append(f" Bearish OB deckt Range-High = Supply Zone")
                else:
                    score += 4
                    details.append(f" Bearish OBs vorhanden ({len(bear_obs)}x) aber nicht in Naehe")
            else:
                details.append(f" Keine Bearish Order Blocks")
    except Exception:
        details.append(" Order Block Check uebersprungen")

    # ===================================================================
    # SIGNAL 16: VOLUME IMBALANCE / FVG PROXIMITY — max 10 Punkte
    # FVG ueber Preis = Magnet fuer Long, unter Preis = Magnet fuer Short
    # ===================================================================
    try:
        vi_data = detect_volume_imbalances(bars, max_zones=20)
        if direction == "long" and vi_data.get("unfilled_bull"):
            # Unfilled Bullish FVG über aktuellem Preis = Preis wird hingezogen
            # V68: Direkt zone_low verwenden (detect_volume_imbalances gibt immer zone_low zurück)
            above_fvgs = [z for z in vi_data["unfilled_bull"] if z.get("zone_low", current_price) > current_price]
            if above_fvgs:
                score += 10
                details.append(f" {len(above_fvgs)} unfilled FVGs ueber Preis = Breakout-Magneten")
            elif vi_data["unfilled_bull"]:
                score += 4
                details.append(f" Bullish FVGs vorhanden ({len(vi_data['unfilled_bull'])}x)")
            else:
                details.append(f" Keine bullischen FVGs")
        elif direction == "short" and vi_data.get("unfilled_bear"):
            # V68: Direkt zone_high verwenden (keine fragilen Fallback-Chains)
            below_fvgs = [z for z in vi_data["unfilled_bear"] if z.get("zone_high", current_price) < current_price]
            if below_fvgs:
                score += 10
                details.append(f" {len(below_fvgs)} unfilled FVGs unter Preis = Breakdown-Magneten")
            elif vi_data["unfilled_bear"]:
                score += 4
                details.append(f" Bearish FVGs vorhanden ({len(vi_data['unfilled_bear'])}x)")
            else:
                details.append(f" Keine baerischen FVGs")
        else:
            details.append(f" Keine relevanten FVGs")
    except Exception:
        details.append(" FVG Check uebersprungen")

    # ===================================================================
    # SIGNAL 17: LIQUIDITY POOL PROXIMITY — max 14 Punkte [BOOSTED]
    # Buyside Liq ueber Range = Stop-Hunt → explosiver Move (ICT/Wyckoff)
    # ===================================================================
    try:
        liq_data = detect_liquidity_levels(bars, max_levels=5)
        range_high_17 = max(highs[-min(15, n):])
        range_low_17 = min(lows[-min(15, n):])

        if direction == "long" and liq_data.get("buyside"):
            # V68 Fix: Key "level" statt "price" (detect_liquidity_levels gibt "level" zurück)
            near_liq = [l for l in liq_data["buyside"] if l["level"] > range_high_17 and current_price > 0 and (l["level"] - range_high_17) / current_price * 100 < 3]
            if near_liq:
                score += 14; sm_fires += 1; sm_hits += 1
                details.append(f" Buyside Liquidity {near_liq[0]['level']:.2f} knapp ueber Range = Stop-Hunt Potential")
            elif liq_data["buyside"]:
                score += 5; sm_hits += 1
                details.append(f" Buyside Liq vorhanden ({len(liq_data['buyside'])} Levels)")
            else:
                details.append(f" Keine Buyside Liquidity erkannt")
        elif direction == "short" and liq_data.get("sellside"):
            near_liq = [l for l in liq_data["sellside"] if l["level"] < range_low_17 and current_price > 0 and (range_low_17 - l["level"]) / current_price * 100 < 3]
            if near_liq:
                score += 14; sm_fires += 1; sm_hits += 1
                details.append(f" Sellside Liquidity {near_liq[0]['level']:.2f} knapp unter Range = Stop-Hunt Potential")
            elif liq_data["sellside"]:
                score += 5; sm_hits += 1
                details.append(f" Sellside Liq vorhanden ({len(liq_data['sellside'])} Levels)")
            else:
                details.append(f" Keine Sellside Liquidity erkannt")
        else:
            details.append(f" Keine relevanten Liquidity Levels")
    except Exception:
        details.append(" Liquidity Check uebersprungen")

    # ===================================================================
    # SIGNAL 18: FIBONACCI CONFLUENCE — max 10 Punkte
    # Preis nahe Key-Fib-Level = starker Breakout-Punkt
    # ===================================================================
    if n >= 20:
        # Finde Swing High/Low der letzten 30 Bars fuer Fib
        lookback = min(30, n)
        swing_high = max(highs[-lookback:])
        swing_low = min(lows[-lookback:])
        fib_range = swing_high - swing_low

        if fib_range > 0 and current_price > 0:
            # Alle Fib-Level berechnen
            fib_levels = {
                "23.6%": swing_high - fib_range * 0.236,
                "38.2%": swing_high - fib_range * 0.382,
                "50.0%": swing_high - fib_range * 0.500,
                "61.8%": swing_high - fib_range * 0.618,
                "78.6%": swing_high - fib_range * 0.786,
            }
            tolerance = fib_range * 0.03  # 3% der Range als Toleranz

            # Direktional filtern: Long = obere Fibs (23.6%, 38.2%), Short = untere Fibs (61.8%, 78.6%)
            if direction == "long":
                # Für Long-Breakout: Preis sollte nahe 23.6% oder 38.2% sein (obere Range)
                bullish_fibs = {"23.6%": fib_levels["23.6%"], "38.2%": fib_levels["38.2%"]}
                near_fibs = [name for name, level in bullish_fibs.items() if abs(current_price - level) < tolerance]
                # Auch 50% akzeptieren (mittlere Stärke)
                if not near_fibs and abs(current_price - fib_levels["50.0%"]) < tolerance:
                    near_fibs = ["50.0%"]
            else:
                # Für Short-Breakdown: Preis sollte nahe 61.8% oder 78.6% sein (untere Range)
                bearish_fibs = {"61.8%": fib_levels["61.8%"], "78.6%": fib_levels["78.6%"]}
                near_fibs = [name for name, level in bearish_fibs.items() if abs(current_price - level) < tolerance]
                if not near_fibs and abs(current_price - fib_levels["50.0%"]) < tolerance:
                    near_fibs = ["50.0%"]

            if near_fibs:
                score += 10
                details.append(f" Fib-Confluence: Preis nahe {', '.join(near_fibs)} ({'bullisch' if direction == 'long' else 'baerisch'})")
            else:
                # Prüfe ob Range-Boundary nahe Fib
                range_h = max(highs[-min(15, n):])
                range_l = min(lows[-min(15, n):])
                boundary = range_h if direction == "long" else range_l
                near_boundary_fibs = [name for name, level in fib_levels.items() if abs(boundary - level) < tolerance]
                if near_boundary_fibs:
                    score += 5
                    details.append(f" Range-Boundary nahe Fib {', '.join(near_boundary_fibs)}")
                else:
                    details.append(f" Kein relevantes Fib-Level in der Naehe")
        else:
            details.append(f" Fib: Range zu klein")
    else:
        details.append(" Fib: Nicht genug Daten (min 20 Tage)")

    # ===================================================================
    # SIGNAL 19: VOLUME PROFILE VOID / PRICE GAP — max 10 Punkte
    # Crypto: Price Gap Detector — Preiszonen mit wenig Aktivität (Preis fliegt durch)
    # ===================================================================
    if crypto_mode:
        try:
            # Price Distribution: Histogramm der Close-Preise
            price_min = min(lows)
            price_max = max(highs)
            price_range = price_max - price_min
            if price_range > 0 and n >= 10:
                num_bins = 15
                bin_size = price_range / num_bins
                bins = [0] * num_bins
                for b in bars:
                    mid_price = (b["high"] + b["low"]) / 2
                    bin_idx = min(int((mid_price - price_min) / bin_size), num_bins - 1)
                    bins[bin_idx] += 1
                # Finde Voids (Bins mit < 20% des Durchschnitts)
                avg_density = sum(bins) / num_bins
                current_bin = min(int((current_price - price_min) / bin_size), num_bins - 1)
                void_above = False
                void_below = False
                for bi in range(current_bin + 1, min(current_bin + 4, num_bins)):
                    if bins[bi] < avg_density * 0.2:
                        void_above = True
                        break
                for bi in range(max(current_bin - 3, 0), current_bin):
                    if bins[bi] < avg_density * 0.2:
                        void_below = True
                        break
                if direction == "long" and void_above:
                    score += 10
                    details.append(f" Price Void ueber Preis = wenig Widerstand")
                elif direction == "short" and void_below:
                    score += 10
                    details.append(f" Price Void unter Preis = wenig Support")
                elif direction == "long" and void_below:
                    score += 3
                    details.append(f" Price Void nur unter Preis")
                elif direction == "short" and void_above:
                    score += 3
                    details.append(f" Price Void nur ueber Preis")
                else:
                    details.append(f" Kein Price Void in der Naehe")
            else:
                details.append(" Price Gap: Nicht genug Daten")
        except Exception:
            details.append(" Price Gap Check uebersprungen")
    else:
        try:
            vol_profile = calculate_volume_profile(bars, num_bins=15)
            if vol_profile:
                void_data = find_volume_voids(current_price, vol_profile, min_void_size_pct=0.5)
                if void_data:
                    if direction == "long" and void_data.get("voids_above"):
                        nearest = void_data["nearest_void_above"]
                        if nearest:
                            dist_pct = (nearest["low"] - current_price) / current_price * 100 if current_price > 0 else 99
                            if dist_pct < 5:
                                score += 10
                                details.append(f" Volume Void {dist_pct:.1f}% ueber Preis = Vakuum-Effekt!")
                            elif dist_pct < 10:
                                score += 5
                                details.append(f" Volume Void {dist_pct:.1f}% entfernt")
                            else:
                                details.append(f" Volume Void zu weit: {dist_pct:.1f}%")
                        else:
                            details.append(f" Kein Volume Void ueber Preis")
                    elif direction == "short" and void_data.get("voids_below"):
                        nearest = void_data["nearest_void_below"]
                        if nearest:
                            dist_pct = (current_price - nearest["high"]) / current_price * 100 if current_price > 0 else 99
                            if dist_pct < 5:
                                score += 10
                                details.append(f" Volume Void {dist_pct:.1f}% unter Preis = Vakuum-Effekt!")
                            elif dist_pct < 10:
                                score += 5
                                details.append(f" Volume Void {dist_pct:.1f}% entfernt")
                            else:
                                details.append(f" Volume Void zu weit: {dist_pct:.1f}%")
                        else:
                            details.append(f" Kein Volume Void unter Preis")
                    else:
                        details.append(f" Kein relevanter Volume Void")
                else:
                    details.append(f" Volume Void Analyse leer")
            else:
                details.append(f" Volume Profile nicht berechenbar")
        except Exception:
            details.append(" Volume Void Check uebersprungen")

    # ===================================================================
    # SIGNAL 20: CANDLE BODY COMPRESSION — max 5 Punkte [CUT]
    # Body-Ratio sinkt = Doji-Cluster, ABER auch bei Aktien ohne Interesse!
    # ===================================================================
    if n >= 10:
        # Vergleiche Body/Range Ratio: letzte 5 vs vorherige 10
        def _body_ratio(b):
            rng = b["high"] - b["low"]
            if rng <= 0:
                return 1.0
            return abs(b["close"] - b["open"]) / rng

        recent_body_ratios = [_body_ratio(b) for b in bars[-5:]]
        prior_body_ratios = [_body_ratio(b) for b in bars[-15:-5]] if n >= 15 else [_body_ratio(b) for b in bars[:-5]]

        avg_recent_body = sum(recent_body_ratios) / len(recent_body_ratios) if recent_body_ratios else 0.5
        avg_prior_body = sum(prior_body_ratios) / len(prior_body_ratios) if prior_body_ratios else 0.5

        if avg_prior_body > 0:
            body_compression = avg_recent_body / avg_prior_body

            if body_compression < 0.4:
                score += 5
                details.append(f" Extreme Body-Kompression: {body_compression:.2f}x (Doji-Cluster!)")
            elif body_compression < 0.6:
                score += 3
                details.append(f" Starke Body-Kompression: {body_compression:.2f}x")
            elif body_compression < 0.8:
                score += 1
                details.append(f" Leichte Body-Kompression: {body_compression:.2f}x")
            else:
                details.append(f" Keine Body-Kompression: {body_compression:.2f}x")

    # ===================================================================
    # FIX 4: RSI + STOCHASTIC DEDUP — Take max() of both momentum signals
    # Don't double-count momentum if both RSI and Stochastic signal the same condition
    # ===================================================================
    momentum_points = max(rsi_points, stoch_points)
    score += momentum_points
    # Add whichever detail is applicable (prefer higher-scoring one)
    if stoch_points > rsi_points and stoch_detail:
        details.append(stoch_detail)
    elif rsi_detail:
        details.append(rsi_detail)
    if stoch_detail and rsi_detail and rsi_points > 0 and stoch_points > 0:
        # If both have points, add a note that we took the max
        details.append(f"  [Dedup: max({rsi_points}, {stoch_points}) = {momentum_points} to avoid double-counting momentum]")

    # ===================================================================
    # FINAL SCORE + RICHTUNGS-KONFIDENZ + GRADE + SMART MONEY SUB-SCORE
    # ===================================================================
    # Tatsächliche Summe der Signal-Maxima (nach FIX 1 + FIX 4):
    # 6+5+13+10+5+10+14+7+5+10+14+6+10+max(5,10)+14+10+14+10+10+5 = 188
    # FIX 1: Signal 8 (14→7) + Signal 9 (10→5) = -12 pts from OG 200
    # FIX 4: RSI+Stoch take max() instead of sum → often saves ~5 pts on average
    # (BOOSTED-Signale: 14 Punkte max, CUT-Signale: 5-6 Punkte max)
    max_score = 188

    # Richtungs-Konfidenz: Wie viele von 20 Signalen sind positiv?
    # Nutze feste Basis 20 (nicht len(details)) um keine künstliche Inflation
    # V2.7: Fix — "" in d war immer True (Emojis verloren beim Refactoring)
    # Statt Emoji-Suche: Score-basierte Konfidenz (akkurater als String-Matching)
    direction_confidence = round((score / max_score) * 100) if max_score > 0 else 0

    # Smart Money Sub-Score: Inline-Counter sm_fires/sm_hits werden direkt
    # bei jedem BOOSTED-Signal inkrementiert (Signale 3,7,8,11,15,17)
    # V3: Bei zu wenig Volume (avg <100K) sind SM-Signale Rauschen → auf 0 setzen
    # Score bleibt erhalten (Signale sind trotzdem mathematisch korrekt),
    # aber sm_fires/sm_hits = 0 → kein Grade A/B möglich für illiquide Aktien
    if sm_eligible:
        smart_money_fires = sm_fires
        smart_money_hits = sm_hits
    else:
        smart_money_fires = 0
        smart_money_hits = 0

    # Grade System V2.5 — Score + Smart Money kombiniert
    # Höhere Grades brauchen BEIDES: hohen Score UND Smart Money Signale
    # V69.1 AUDIT FIX: Crypto-Schwellen leicht gesenkt (-5pts pro Grade).
    # Crypto hat kein echtes Volume → weniger erreichbare Punkte als Aktien.
    # Signal 2 misst jetzt Body-Kompression (nicht mehr Spread-Duplikat von Signal 1),
    # was die Punkte-Verteilung etwas anders gewichtet.
    # V2.9: Crypto-Schwellen angehoben — waren 41% unter Aktien (Grade-Inflation)
    # Jetzt ~15% unter Aktien (Crypto hat weniger Volume-Signale, aber nicht 41% weniger)
    if crypto_mode:
        if score >= 95 and smart_money_fires >= 3:
            grade = "S"
        elif score >= 85 and smart_money_fires >= 2:
            grade = "A"
        elif score >= 72 and smart_money_hits >= 1:
            grade = "B"
        elif score >= 60:
            grade = "C"
        else:
            grade = "D"
    else:
        # V2.8: Grading — Original-Logik mit SM-Bestätigung für jedes Grade
        # Professionelles Trading: Score allein reicht nicht, Smart Money muss bestätigen
        # sm_fires = Boosted Signals auf Maximum (stärkste Bestätigung)
        # sm_hits = Boosted Signals aktiv (moderate Bestätigung)
        #
        # Original hatte: S>=120+4fires, A>=105+3fires, B>=90+2hits, C>=80
        # Proportional skaliert für max_score 188 (statt 200):
        # 120/200 = 60% → 113/188, 105/200 = 52.5% → 99/188, 90/200 = 45% → 85/188
        # SM-Anforderungen: Original-Werte beibehalten (diese sind score-unabhängig)
        if score >= 113 and smart_money_fires >= 4:
            grade = "S"  # ELITE — Top 60% Score + 4 Boosted fires
        elif score >= 99 and smart_money_fires >= 3:
            grade = "A"  # STARK — Top 52.5% Score + 3 Boosted fires
        elif score >= 85 and smart_money_hits >= 2:
            grade = "B"  # SOLIDE — Top 45% Score + 2 SM hits (Original!)
        elif score >= 75:
            grade = "C"  # WATCHLIST — Score 75+
        else:
            grade = "D"  # SCHWACH

    # Threshold: V4 — nach kumulativen Audit-Korrekturen (Wyckoff-Boundary, Signal8-Reduktion,
    # Higher-Lows-Threshold, RSI/Stoch-Dedup, OBV-Neutral-Fix) sind ~28 Punkte weniger erreichbar.
    # Alter Threshold 65/60 war VOR diesen Fixes kalibriert → ergab NULL Resultate.
    # Neu: 45 Long (23.9%), 40 Short (21.3%) — kalibriert für 50+ Ergebnisse.
    # Eine marginale Breakout-Aktie erzielt ~50 Punkte nach Audit-Korrekturen.
    # Qualitätskontrolle: Grading (A/B brauchen SM-Fires) + Score-Bucket Tracking.
    if crypto_mode:
        threshold = 35 if direction == "long" else 30
    else:
        threshold = 45 if direction == "long" else 40
    is_valid = score >= threshold

    # Cap score at max_score to prevent overflow
    score = min(score, max_score)

    return is_valid, score, max_score, details, direction_confidence, grade, smart_money_fires, smart_money_hits


# ── find_pivots (originally line 4246) ──
def find_pivots(prices, window=5):
    """
    Findet Swing Highs und Swing Lows (Pivot Points) mit ZigZag-Logik.
    
    Args:
        prices: Liste von Dictionaries mit 'high', 'low', 'close', 'date'
        window: Anzahl Kerzen links/rechts für Pivot-Bestätigung
    
    Returns:
        Liste von Pivots: [{'type': 'high'/'low', 'price': x, 'index': i, 'date': d}, ...]
    """
    if len(prices) < window * 2 + 1:
        return []
    
    pivots = []
    
    for i in range(window, len(prices) - window):
        # Prüfe Swing High
        is_swing_high = True
        current_high = prices[i]['high']
        for j in range(i - window, i + window + 1):
            if j != i and prices[j]['high'] >= current_high:
                is_swing_high = False
                break
        
        if is_swing_high:
            pivots.append({
                'type': 'high',
                'price': current_high,
                'index': i,
                'date': prices[i].get('date', '')
            })
            continue  # Ein Punkt kann nicht beides sein
        
        # Prüfe Swing Low
        is_swing_low = True
        current_low = prices[i]['low']
        for j in range(i - window, i + window + 1):
            if j != i and prices[j]['low'] <= current_low:
                is_swing_low = False
                break
        
        if is_swing_low:
            pivots.append({
                'type': 'low',
                'price': current_low,
                'index': i,
                'date': prices[i].get('date', '')
            })
    
    # === EDGE PIVOT: Prüfe letzten Abschnitt (letzte window Bars) ===
    # Ohne das wird der D-Punkt (Pattern-Completion!) abgeschnitten
    if len(prices) > window + 1:
        edge_start = len(prices) - window
        edge_section = prices[edge_start:]
        left_section = prices[max(0, edge_start - window):edge_start]
        
        if left_section:
            # Finde höchstes High und niedrigstes Low in den letzten bars
            edge_high_val = max(p['high'] for p in edge_section)
            edge_low_val = min(p['low'] for p in edge_section)
            left_high = max(p['high'] for p in left_section)
            left_low = min(p['low'] for p in left_section)
            
            # Edge Swing High: Höher als alle Bars links davon
            if edge_high_val > left_high:
                edge_idx = edge_start + max(range(len(edge_section)), key=lambda k: edge_section[k]['high'])
                # Nur hinzufügen wenn nicht Duplikat vom letzten Pivot
                if not pivots or (pivots[-1]['type'] != 'high' or abs(pivots[-1]['price'] - edge_high_val) > 0.01):
                    pivots.append({
                        'type': 'high', 'price': edge_high_val,
                        'index': edge_idx, 'date': prices[edge_idx].get('date', '')
                    })
            
            # Edge Swing Low: Tiefer als alle Bars links davon
            elif edge_low_val < left_low:
                edge_idx = edge_start + min(range(len(edge_section)), key=lambda k: edge_section[k]['low'])
                if not pivots or (pivots[-1]['type'] != 'low' or abs(pivots[-1]['price'] - edge_low_val) > 0.01):
                    pivots.append({
                        'type': 'low', 'price': edge_low_val,
                        'index': edge_idx, 'date': prices[edge_idx].get('date', '')
                    })
    
    return pivots


# ── check_fibonacci_ratio (originally line 4332) ──
def check_fibonacci_ratio(actual, target, tolerance=0.05):
    """
    Prüft ob ein Verhältnis innerhalb der Toleranz liegt.
    V3.4 FIX: Toleranz ist jetzt PROZENTUAL (relativ zum Target).
    Vorher absolut → bei hohen Targets (Crab 2.618) war Toleranz viel zu eng.

    Args:
        actual: Berechnetes Verhältnis
        target: Ziel-Fibonacci-Level (z.B. 0.618)
        tolerance: Erlaubte prozentuale Abweichung (0.05 = 5%)

    Returns:
        (is_valid, deviation_pct)
    """
    deviation = abs(actual - target)
    deviation_pct = (deviation / target) * 100 if target > 0 else 100
    # V3.4: Prozentuale Toleranz statt absolut (5% = tolerance von 0.05)
    is_valid = deviation_pct <= (tolerance * 100) if target > 0 else False
    return is_valid, deviation_pct


# ── HARMONIC_PATTERNS (originally line 4351) ──
HARMONIC_PATTERNS = {
    "Gartley": {
        "emoji": "",
        "description": "Klassisches Harmonic Pattern mit hoher Erfolgsrate",
        "ratios": {
            "AB_XA": (0.618, 0.03),      # AB = 61.8% von XA (±3%)
            "BC_AB": (0.382, 0.886, 0.03), # BC = 38.2-88.6% von AB
            "CD_BC": (1.272, 1.618, 0.03), # CD = 127.2-161.8% von BC
            "AD_XA": (0.786, 0.03),       # D = 78.6% Retracement von XA
        },
        "success_rate": 70,
        "target_ratios": [0.382, 0.618]  # Profit Targets
    },
    "Butterfly": {
        "emoji": "",
        "description": "Extension Pattern - D geht über X hinaus",
        "ratios": {
            "AB_XA": (0.786, 0.03),
            "BC_AB": (0.382, 0.886, 0.03),
            "CD_BC": (1.618, 2.618, 0.03),
            "AD_XA": (1.272, 1.618, 0.03),  # D extends beyond X
        },
        "success_rate": 65,
        "target_ratios": [0.382, 0.618, 1.0]
    },
    "Bat": {
        "emoji": "",
        "description": "Tiefes Retracement Pattern",
        "ratios": {
            "AB_XA": (0.382, 0.5, 0.03),
            "BC_AB": (0.382, 0.886, 0.03),
            "CD_BC": (1.618, 2.618, 0.03),
            "AD_XA": (0.886, 0.03),
        },
        "success_rate": 70,
        "target_ratios": [0.382, 0.618]
    },
    "Crab": {
        "emoji": "",
        "description": "Extremes Extension Pattern",
        "ratios": {
            "AB_XA": (0.382, 0.618, 0.03),
            "BC_AB": (0.382, 0.886, 0.03),
            "CD_BC": (2.24, 3.618, 0.03),
            "AD_XA": (1.618, 0.03),
        },
        "success_rate": 60,
        "target_ratios": [0.382, 0.618]
    },
    "Shark": {
        "emoji": "",
        "description": "Aggressives Reversal Pattern",
        "ratios": {
            "AB_XA": (0.446, 0.618, 0.03),
            "BC_AB": (1.13, 1.618, 0.03),
            "CD_BC": (1.618, 2.24, 0.03),
            "AD_XA": (0.886, 1.13, 0.03),
        },
        "success_rate": 55,
        "target_ratios": [0.5, 0.886]
    }
}


# ── identify_harmonic_pattern (originally line 4415) ──
def identify_harmonic_pattern(pivots, prices, min_pivots=5):
    """
    Identifiziert Harmonic Patterns aus Pivot Points.
    
    Args:
        pivots: Liste von Pivot Points
        prices: Original Preisdaten
        min_pivots: Minimum Anzahl Pivots für Pattern
    
    Returns:
        Liste von erkannten Patterns mit Score und Details
    """
    if len(pivots) < min_pivots:
        return []
    
    patterns_found = []
    
    # Suche nach XABCD Sequenzen (letzte 5 Pivots)
    # Pattern muss alternieren: High-Low-High-Low-High oder Low-High-Low-High-Low
    for i in range(len(pivots) - 4):
        potential_xabcd = pivots[i:i+5]
        
        # Prüfe Alternierung
        types = [p['type'] for p in potential_xabcd]
        alternates = all(types[j] != types[j+1] for j in range(4))
        if not alternates:
            continue
        
        # Extrahiere XABCD Preise
        X = potential_xabcd[0]['price']
        A = potential_xabcd[1]['price']
        B = potential_xabcd[2]['price']
        C = potential_xabcd[3]['price']
        D = potential_xabcd[4]['price']
        
        # Bestimme Richtung (bullish = X ist Low, bearish = X ist High)
        is_bullish = potential_xabcd[0]['type'] == 'low'
        
        # Berechne Fibonacci Verhältnisse
        xa_move = abs(A - X)
        if xa_move == 0:
            continue

        ab_retracement = abs(B - A) / xa_move

        ab_move = abs(B - A)
        if ab_move == 0:
            continue
        bc_retracement = abs(C - B) / ab_move
        
        bc_move = abs(C - B)
        if bc_move == 0:
            continue
        cd_extension = abs(D - C) / bc_move
        
        ad_retracement = abs(D - A) / xa_move
        
        # Prüfe gegen alle Pattern-Definitionen
        for pattern_name, pattern_def in HARMONIC_PATTERNS.items():
            score = 0
            details = []
            matches = 0
            total_checks = 0
            
            ratios = pattern_def["ratios"]
            
            # V3.4 FIX: Range-Check mit prozentualer Toleranz statt absolut
            def _range_check(actual, min_val, max_val, tol):
                """Prüft ob actual in Range liegt, mit prozentualer Toleranz."""
                tol_low = min_val * tol if min_val > 0 else tol
                tol_high = max_val * tol if max_val > 0 else tol
                return (min_val - tol_low) <= actual <= (max_val + tol_high)

            # AB/XA Check
            if "AB_XA" in ratios:
                target = ratios["AB_XA"]
                if len(target) == 2:  # Single value
                    target_val, tol = target
                    is_valid, dev = check_fibonacci_ratio(ab_retracement, target_val, tol)
                else:  # Range
                    min_val, max_val, tol = target
                    is_valid = _range_check(ab_retracement, min_val, max_val, tol)
                    dev = 0 if is_valid else min(abs(ab_retracement - min_val), abs(ab_retracement - max_val)) * 100

                total_checks += 1
                if is_valid:
                    matches += 1
                    score += 20
                    details.append(f" AB/XA: {ab_retracement:.3f}")
                else:
                    details.append(f" AB/XA: {ab_retracement:.3f}")

            # BC/AB Check
            if "BC_AB" in ratios:
                target = ratios["BC_AB"]
                if len(target) == 2:
                    target_val, tol = target
                    is_valid, dev = check_fibonacci_ratio(bc_retracement, target_val, tol)
                else:
                    min_val, max_val, tol = target
                    is_valid = _range_check(bc_retracement, min_val, max_val, tol)

                total_checks += 1
                if is_valid:
                    matches += 1
                    score += 20
                    details.append(f" BC/AB: {bc_retracement:.3f}")
                else:
                    details.append(f" BC/AB: {bc_retracement:.3f}")

            # CD/BC Check
            if "CD_BC" in ratios:
                target = ratios["CD_BC"]
                if len(target) == 2:
                    target_val, tol = target
                    is_valid, dev = check_fibonacci_ratio(cd_extension, target_val, tol)
                else:
                    min_val, max_val, tol = target
                    is_valid = _range_check(cd_extension, min_val, max_val, tol)
                
                total_checks += 1
                if is_valid:
                    matches += 1
                    score += 25
                    details.append(f" CD/BC: {cd_extension:.3f}")
                else:
                    details.append(f" CD/BC: {cd_extension:.3f}")
            
            # AD/XA Check (wichtigstes Verhältnis!)
            if "AD_XA" in ratios:
                target = ratios["AD_XA"]
                if len(target) == 2:
                    target_val, tol = target
                    is_valid, dev = check_fibonacci_ratio(ad_retracement, target_val, tol)
                else:
                    min_val, max_val, tol = target
                    is_valid = _range_check(ad_retracement, min_val, max_val, tol)
                
                total_checks += 1
                if is_valid:
                    matches += 1
                    score += 35  # Höhere Gewichtung
                    details.append(f" AD/XA: {ad_retracement:.3f}")
                else:
                    details.append(f" AD/XA: {ad_retracement:.3f}")
            
            # Pattern gilt als erkannt wenn mindestens 3/4 Verhältnisse stimmen
            if matches >= 3 and score >= 50:
                # Berechne Entry, Stop Loss, Take Profits
                if is_bullish:
                    entry = D
                    stop_loss = D * 0.97  # 3% unter D
                    tp1 = D + (C - D) * 0.382
                    tp2 = D + (C - D) * 0.618
                    tp3 = C  # Full retracement
                    direction = "LONG"
                else:
                    entry = D
                    stop_loss = D * 1.03  # 3% über D
                    tp1 = D - (D - C) * 0.382
                    tp2 = D - (D - C) * 0.618
                    tp3 = C
                    direction = "SHORT"

                # ── DISTANZ-CHECK: Wie weit ist Entry vom aktuellen Preis? ──
                current = prices[-1]["close"]
                entry_distance_pct = abs(entry - current) / current * 100 if current > 0 else 0

                # Score-Abzug basierend auf Distanz
                # 0-5%: kein Abzug (Pattern ist aktuell)
                # 5-10%: -15 (Pattern wird alt)
                # 10-20%: -30 (Pattern ist abgelaufen)
                # >20%: -50 (Pattern ist komplett irrelevant)
                distance_penalty = 0
                if entry_distance_pct > 20:
                    distance_penalty = 50
                    details.append(f" Entry {entry_distance_pct:.0f}% vom Preis — ABGELAUFEN")
                elif entry_distance_pct > 10:
                    distance_penalty = 30
                    details.append(f" Entry {entry_distance_pct:.0f}% vom Preis — veraltet")
                elif entry_distance_pct > 5:
                    distance_penalty = 15
                    details.append(f" Entry {entry_distance_pct:.1f}% vom Preis")

                # ── VOLUME CONFIRMATION: D-Punkt sollte über-durchschnittliches Volumen haben ──
                volume_penalty = 0
                d_point_index = potential_xabcd[4]['index']
                if d_point_index < len(prices):
                    d_volume = prices[d_point_index].get('volume', 0)
                    # Berechne Durchschnittsvolumen über das Pattern (X bis D)
                    x_point_index = potential_xabcd[0]['index']
                    pattern_bars = prices[x_point_index:d_point_index+1]
                    avg_volume = sum(p.get('volume', 0) for p in pattern_bars) / len(pattern_bars) if pattern_bars else 1

                    # Wenn D-Volumen unter Durchschnitt: -20 Punkte
                    if d_volume < avg_volume and avg_volume > 0:
                        volume_penalty = 20
                        details.append(f" D-Punkt Volumen schwach ({d_volume:.0f} vs avg {avg_volume:.0f})")

                # ── AGE PENALTY: Lange Patterns sind weniger zuverlässig ──
                age_penalty = 0
                x_point_index = potential_xabcd[0]['index']
                d_point_index = potential_xabcd[4]['index']
                num_bars = d_point_index - x_point_index

                # Wenn Pattern > 200 Bars dauert, reduziere Score mit age_factor
                if num_bars > 100:
                    age_factor = max(0.5, 1.0 - (num_bars - 100) / 400.0)
                    age_penalty = int((1.0 - age_factor) * 30)  # Max 30 Punkte Abzug
                    details.append(f" Pattern-Alter: {num_bars} Bars (Faktor {age_factor:.2f})")

                adjusted_score = max(0, score - distance_penalty - volume_penalty - age_penalty)

                patterns_found.append({
                    "pattern": pattern_name,
                    "emoji": pattern_def["emoji"],
                    "direction": direction,
                    "score": adjusted_score,
                    "raw_score": score,
                    "entry_distance_pct": round(entry_distance_pct, 1),
                    "matches": f"{matches}/{total_checks}",
                    "success_rate": pattern_def["success_rate"],
                    "details": details,
                    "points": {
                        "X": round(X, 2),
                        "A": round(A, 2),
                        "B": round(B, 2),
                        "C": round(C, 2),
                        "D": round(D, 2)
                    },
                    "ratios": {
                        "AB/XA": round(ab_retracement, 3),
                        "BC/AB": round(bc_retracement, 3),
                        "CD/BC": round(cd_extension, 3),
                        "AD/XA": round(ad_retracement, 3)
                    },
                    "trade": {
                        "entry": round(entry, 2),
                        "stop_loss": round(stop_loss, 2),
                        "tp1": round(tp1, 2),
                        "tp2": round(tp2, 2),
                        "tp3": round(tp3, 2),
                        "risk_reward": round(abs(tp2 - entry) / abs(entry - stop_loss), 2) if abs(entry - stop_loss) > 0 else 0
                    },
                    "pivot_indices": [p['index'] for p in potential_xabcd],
                    "dates": {
                        "X": potential_xabcd[0].get('date', ''),
                        "D": potential_xabcd[4].get('date', '')
                    }
                })
    
    # Sortiere nach Score (beste zuerst)
    patterns_found.sort(key=lambda x: x['score'], reverse=True)
    return patterns_found


# ── scan_harmonic_patterns (originally line 4637) ──
def scan_harmonic_patterns(ticker, api_key, days=180, timeframe="day"):
    """
    Scannt eine Aktie nach Harmonic Patterns.
    
    Args:
        ticker: Aktien-Symbol
        api_key: Polygon API Key
        days: Anzahl Tage historischer Daten
        timeframe: "day" für Daily, "hour" für 4H
    
    Returns:
        Dictionary mit Pattern-Ergebnissen
    """
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 10)
        
        # Polygon Aggregates API
        multiplier = 1
        span = "day"
        if timeframe == "hour":
            multiplier = 4
            span = "hour"
        elif timeframe == "1hour":
            multiplier = 1
            span = "hour"
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{multiplier}/{span}/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        api_limit = 5000 if span == "hour" else 500  # Mehr Bars für Intraday
        params = {"adjusted": "true", "sort": "asc", "limit": api_limit, "apiKey": api_key}
        
        resp = rate_limited_get(url, params=params, timeout=20)
        data = resp.json()
        
        if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
            return {"error": "No data", "patterns": []}
        
        # Konvertiere zu unserem Format
        prices = []
        for bar in data["results"]:
            prices.append({
                "date": datetime.fromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d %H:%M" if span == "hour" else "%Y-%m-%d"),
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar["v"]
            })
        
        if len(prices) < 20:
            return {"error": "Not enough data", "patterns": []}
        
        # Finde Pivots
        pivots = find_pivots(prices, window=3)
        
        if len(pivots) < 5:
            return {"error": "Not enough pivots", "patterns": [], "pivot_count": len(pivots)}
        
        # Identifiziere Patterns
        patterns = identify_harmonic_pattern(pivots, prices)
        
        # Aktueller Preis
        current_price = prices[-1]["close"]
        
        return {
            "ticker": ticker,
            "current_price": current_price,
            "days_analyzed": len(prices),
            "pivots_found": len(pivots),
            "patterns": patterns,
            "last_update": prices[-1]["date"]
        }
        
    except Exception as e:
        return {"error": str(e), "patterns": []}


# ── scan_wyckoff_single (originally line 4766) ──
def scan_wyckoff_single(ticker, api_key, days=180, timeframe="hour"):
    """
    Scannt eine Aktie nach Wyckoff Accumulation/Distribution Patterns.
    Korrekte Wyckoff-Methodik nach Richard Wyckoff / David Weis:
    
    ACCUMULATION (Schematic #1 & #2):
      1. PS  (Preliminary Support): Erste Kaufreaktion nach Downtrend, Volume steigt
      2. SC  (Selling Climax): Wide Spread DOWN + Ultra-High Volume + Close obere Haelfte (Absorption!)
      3. AR  (Automatic Rally): Schneller Bounce, definiert Oberkante der Range
      4. ST  (Secondary Test): Retest SC-Zone auf ABNEHMENDEM Volume, Spread enger
      5. Spring/Shakeout: Kurzer Bruch unter SC-Low auf LOW Volume -> schnelle Recovery
      6. Test of Spring: Retest Spring-Low auf noch niedrigerem Volume
      7. SOS (Sign of Strength): Wide Spread UP + HIGH Volume ueber AR-Level
      8. LPS (Last Point of Support): Pullback auf ABNEHMENDEM Volume, haelt ueber alter Resistance
    
    DISTRIBUTION (Schematic #1 & #2):
      1. PSY (Preliminary Supply): Erste Verkaufsreaktion nach Uptrend
      2. BC  (Buying Climax): Wide Spread UP + Ultra-High Volume + Close untere Haelfte
      3. AR  (Automatic Reaction): Schneller Drop, definiert Unterkante der Range
      4. ST  (Secondary Test): Retest BC-Zone auf abnehmendem Volume
      5. UTAD (Upthrust After Distribution): Bruch ueber BC-High auf LOW Volume -> Failure
      6. SOW (Sign of Weakness): Wide Spread DOWN + HIGH Volume unter AR-Level
      7. LPSY (Last Point of Supply): Rally auf abnehmendem Volume, scheitert unter alter Support
    
    KRITISCH: Volume + Spread (Kerzengroesse) zusammen analysieren!
    """
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 10)
        
        multiplier = 4
        span = "hour"
        if timeframe == "day":
            multiplier = 1
            span = "day"
        elif timeframe == "1hour":
            multiplier = 1
            span = "hour"
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{multiplier}/{span}/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        api_limit = 5000 if span == "hour" else 500
        params = {"adjusted": "true", "sort": "asc", "limit": api_limit, "apiKey": api_key}
        
        resp = rate_limited_get(url, params=params, timeout=20)
        data = resp.json()
        
        if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
            return None
        
        raw_bars = data["results"]
        if len(raw_bars) < 60:
            return None
        
        opens = [b["o"] for b in raw_bars]
        closes = [b["c"] for b in raw_bars]
        highs = [b["h"] for b in raw_bars]
        lows = [b["l"] for b in raw_bars]
        volumes = [b["v"] for b in raw_bars]
        current_price = closes[-1]
        n = len(closes)
        
        # Helper: Spread (Kerzengroesse) und Body-Position
        def spread(i):
            return highs[i] - lows[i]
        
        def body_position(i):
            """Wo schliesst die Kerze relativ zum Range? 0=Low, 1=High"""
            s = spread(i)
            return (closes[i] - lows[i]) / s if s > 0 else 0.5
        
        def is_wide_spread(i, lookback=20):
            """Spread > 1.3x Durchschnitt"""
            start = max(0, i - lookback)
            avg_spread = sum(spread(j) for j in range(start, i)) / max(1, i - start)
            return spread(i) > avg_spread * 1.3
        
        def is_high_volume(i, lookback=20):
            """Volume > 1.5x Durchschnitt"""
            start = max(0, i - lookback)
            avg_vol = sum(volumes[start:i]) / max(1, i - start)
            return volumes[i] > avg_vol * 1.5, volumes[i] / max(1, avg_vol)
        
        def is_low_volume(i, lookback=20):
            """Volume < 0.7x Durchschnitt"""
            start = max(0, i - lookback)
            avg_vol = sum(volumes[start:i]) / max(1, i - start)
            return volumes[i] < avg_vol * 0.7, volumes[i] / max(1, avg_vol)
        
        # ATR fuer Stop-Berechnung
        atr_values = []
        for i in range(1, n):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            atr_values.append(tr)
        atr = sum(atr_values[-14:]) / min(14, len(atr_values)) if atr_values else current_price * 0.02
        
        results = []
        lookback_end = int(n * 0.5)
        
        # ================================================================
        # ACCUMULATION: Suche SC (Selling Climax) — DAS definiert die Range
        # ================================================================
        sc_candidates = []
        for i in range(10, lookback_end):
            hi_vol, vol_ratio = is_high_volume(i)
            if not hi_vol or vol_ratio < 1.8:
                continue
            if not is_wide_spread(i):
                continue
            # SC: Close muss in oberer Haelfte sein (Absorption durch Smart Money)
            # 0.45 = Close mindestens nahe Mitte der Kerze (strenger als 0.35)
            if body_position(i) < 0.45:
                continue
            prior_high = max(highs[max(0, i-15):i])
            decline_pct = (prior_high - lows[i]) / prior_high if prior_high > 0 else 0
            if decline_pct < 0.05:
                continue
            sc_candidates.append({
                "idx": i, "price": lows[i], "close": closes[i],
                "vol_ratio": vol_ratio, "decline": decline_pct,
                "score": vol_ratio * 10 + decline_pct * 100
            })
        
        if sc_candidates:
            sc_candidates.sort(key=lambda x: x["score"], reverse=True)
            sc = sc_candidates[0]
            sc_idx = sc["idx"]
            sc_low = sc["price"]
            
            # PS (Preliminary Support): VOR SC — erste Kaufreaktion
            ps_idx = None
            for i in range(max(5, sc_idx - 20), sc_idx):
                hi_vol, vr = is_high_volume(i)
                if hi_vol and closes[i] > opens[i] and body_position(i) > 0.5:
                    ps_idx = i
                    break
            
            # AR (Automatic Rally): Hoechster Punkt 3-20 Bars NACH SC
            ar_idx, ar_high = None, 0
            for i in range(sc_idx + 2, min(sc_idx + 20, n)):
                if highs[i] > ar_high:
                    ar_high = highs[i]
                    ar_idx = i
            
            if ar_idx and ar_high > sc_low:
                range_low = sc_low
                range_high = ar_high
                range_width = range_high - range_low
                range_mid = (range_high + range_low) / 2
                
                if 0.02 < range_width / range_mid < 0.30:
                    events = []
                    event_bars = {}
                    score = 0
                    
                    if ps_idx:
                        events.append(f"PS @ ${closes[ps_idx]:.2f}")
                        event_bars["PS"] = {"idx": ps_idx, "price": closes[ps_idx], "ts": raw_bars[ps_idx]["t"]}
                        score += 10
                    
                    events.append(f"SC @ ${sc_low:.2f} (Vol {sc['vol_ratio']:.1f}x, Close obere Haelfte)")
                    event_bars["SC"] = {"idx": sc_idx, "price": sc_low, "ts": raw_bars[sc_idx]["t"]}
                    score += 20
                    
                    events.append(f"AR @ ${ar_high:.2f}")
                    event_bars["AR"] = {"idx": ar_idx, "price": ar_high, "ts": raw_bars[ar_idx]["t"]}
                    score += 15
                    
                    # Multiple STs auf abnehmendem Volume — Tests nahe SUPPORT (SC-Zone)
                    st_count = 0
                    prev_st_vol = sc["vol_ratio"]
                    for i in range(ar_idx + 3, min(n - 5, ar_idx + int((n - ar_idx) * 0.7))):
                        if lows[i] <= range_low + range_width * 0.25:
                            hi_vol, vr = is_high_volume(i)
                            if vr < prev_st_vol * 0.9:
                                st_label = f"ST{st_count + 1}" if st_count > 0 else "ST"
                                events.append(f"{st_label} @ ${lows[i]:.2f} (Vol {vr:.1f}x)")
                                event_bars[st_label] = {"idx": i, "price": lows[i], "ts": raw_bars[i]["t"]}
                                score += 10 if st_count == 0 else 5
                                prev_st_vol = vr
                                st_count += 1
                                if st_count >= 3:
                                    break
                    
                    # Phase B: Tests nahe RESISTANCE (AR-Zone) — Price rallies TO but fails AT AR
                    # Wichtig: Volume sollte bei Rallies zur Resistance ABNEHMEN
                    rt_count = 0
                    for i in range(ar_idx + 3, min(n - 5, ar_idx + int((n - ar_idx) * 0.7))):
                        if highs[i] >= range_high - range_width * 0.20:
                            hi_vol, vr = is_high_volume(i)
                            # Rally zur Resistance auf normalem/niedrigem Volume = Schwäche
                            if not hi_vol or vr < 1.3:
                                rt_label = f"RT{rt_count + 1}" if rt_count > 0 else "RT"
                                events.append(f"{rt_label} @ ${highs[i]:.2f} (Resistance Test, Vol {vr:.1f}x)")
                                event_bars[rt_label] = {"idx": i, "price": highs[i], "ts": raw_bars[i]["t"]}
                                score += 5
                                rt_count += 1
                                if rt_count >= 2:
                                    break
                    
                    # Volume-Decay in Phase B
                    if ar_idx + 20 < n:
                        early_range_vol = sum(volumes[ar_idx:ar_idx + 10]) / 10
                        mid_point = ar_idx + (n - ar_idx) // 2
                        if mid_point + 10 <= n:
                            later_range_vol = sum(volumes[mid_point:mid_point + 10]) / 10
                            if later_range_vol < early_range_vol * 0.75:
                                events.append(f"Vol Decay: {later_range_vol/early_range_vol:.0%}")
                                score += 5
                    
                    # Spring: Break unter SC-Low auf LOW VOLUME
                    spring_idx = None
                    spring_start = max(ar_idx + 5, int(sc_idx + (n - sc_idx) * 0.3))
                    for i in range(spring_start, n - 3):
                        if lows[i] < range_low:
                            low_vol, lvr = is_low_volume(i)
                            if low_vol or lvr < 0.85:
                                for j in range(1, min(6, n - i)):
                                    if closes[i + j] > range_low + range_width * 0.10:
                                        spring_idx = i
                                        events.append(f"Spring @ ${lows[i]:.2f} (Vol {lvr:.1f}x LOW)")
                                        event_bars["Spring"] = {"idx": i, "price": lows[i], "ts": raw_bars[i]["t"]}
                                        score += 25
                                        break
                            break
                    
                    # Test of Spring
                    if spring_idx and spring_idx + 5 < n:
                        for i in range(spring_idx + 2, min(spring_idx + 15, n)):
                            if lows[i] <= lows[spring_idx] + range_width * 0.05:
                                low_vol, lvr = is_low_volume(i)
                                if low_vol:
                                    events.append(f"Test Spring @ ${lows[i]:.2f} (Vol {lvr:.1f}x)")
                                    event_bars["TestSpring"] = {"idx": i, "price": lows[i], "ts": raw_bars[i]["t"]}
                                    score += 10
                                    break
                    
                    # SOS: Wide Spread UP + HIGH Volume ueber AR-Level
                    sos_idx = None
                    for i in range(max(ar_idx + 10, n - int(n * 0.4)), n):
                        if closes[i] > range_high and closes[i] > opens[i]:
                            hi_vol, vr = is_high_volume(i)
                            if hi_vol and is_wide_spread(i):
                                sos_idx = i
                                events.append(f"SOS @ ${closes[i]:.2f} (Vol {vr:.1f}x, Wide Spread)")
                                event_bars["SOS"] = {"idx": i, "price": closes[i], "ts": raw_bars[i]["t"]}
                                score += 20
                                break
                    
                    if not sos_idx and current_price > range_high:
                        events.append(f"SOS (schwach): ${current_price:.2f} > Range ${range_high:.2f}")
                        score += 8
                    
                    # LPS: Pullback NACH SOS, haelt UEBER range_high, LOW Volume
                    if sos_idx and sos_idx + 3 < n:
                        for i in range(sos_idx + 1, n):
                            if lows[i] < closes[sos_idx]:
                                low_vol, lvr = is_low_volume(i)
                                if lows[i] >= range_high - range_width * 0.10:
                                    if low_vol or lvr < 0.9:
                                        events.append(f"LPS @ ${lows[i]:.2f} (Vol {lvr:.1f}x)")
                                        event_bars["LPS"] = {"idx": i, "price": lows[i], "ts": raw_bars[i]["t"]}
                                        score += 15
                                        break
                    
                    if score >= 35:
                        if sos_idx or current_price > range_high * 1.02:
                            phase = "Phase D/E — Markup beginning"
                            phase_short = "D/E"
                        elif spring_idx:
                            phase = "Phase C — Spring (Smart Money Shakeout)"
                            phase_short = "C"
                        elif st_count >= 1:
                            phase = "Phase B — Cause Building"
                            phase_short = "B"
                        else:
                            phase = "Phase A — Selling Exhaustion"
                            phase_short = "A"
                        
                        if spring_idx:
                            entry = range_high
                            stop = lows[spring_idx] - atr * 0.3
                        else:
                            entry = range_high
                            stop = range_low - atr * 0.5
                        
                        tp1 = range_high + range_width * 0.75
                        tp2 = range_high + range_width * 1.5
                        rr = abs(tp1 - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0
                        
                        results.append({
                            "type": "Accumulation", "direction": "LONG",
                            "phase": phase, "phase_short": phase_short,
                            "score": min(score, 100), "events": events, "event_bars": event_bars,
                            "range_high": round(range_high, 2), "range_low": round(range_low, 2),
                            "range_start_ts": raw_bars[sc_idx]["t"],
                            "range_end_ts": raw_bars[min(n-1, ar_idx + (n - ar_idx) // 2)]["t"],
                            "entry": round(entry, 2), "stop": round(stop, 2),
                            "tp1": round(tp1, 2), "tp2": round(tp2, 2),
                            "rr": round(rr, 2), "current_price": round(current_price, 2),
                        })
        
        # ================================================================
        # DISTRIBUTION: Suche BC (Buying Climax)
        # ================================================================
        bc_candidates = []
        for i in range(10, lookback_end):
            hi_vol, vol_ratio = is_high_volume(i)
            if not hi_vol or vol_ratio < 1.8:
                continue
            if not is_wide_spread(i):
                continue
            if body_position(i) > 0.65:
                continue
            prior_low = min(lows[max(0, i-15):i])
            rally_pct = (highs[i] - prior_low) / prior_low if prior_low > 0 else 0
            if rally_pct < 0.05:
                continue
            bc_candidates.append({
                "idx": i, "price": highs[i], "close": closes[i],
                "vol_ratio": vol_ratio, "rally": rally_pct,
                "score": vol_ratio * 10 + rally_pct * 100
            })
        
        if bc_candidates:
            bc_candidates.sort(key=lambda x: x["score"], reverse=True)
            bc = bc_candidates[0]
            bc_idx = bc["idx"]
            bc_high = bc["price"]
            
            psy_idx = None
            for i in range(max(5, bc_idx - 20), bc_idx):
                hi_vol, vr = is_high_volume(i)
                if hi_vol and closes[i] < opens[i] and body_position(i) < 0.5:
                    psy_idx = i
                    break
            
            ar_idx, ar_low = None, float('inf')
            for i in range(bc_idx + 2, min(bc_idx + 20, n)):
                if lows[i] < ar_low:
                    ar_low = lows[i]
                    ar_idx = i
            
            if ar_idx and ar_low < bc_high:
                range_high = bc_high
                range_low = ar_low
                range_width = range_high - range_low
                range_mid = (range_high + range_low) / 2
                
                if 0.02 < range_width / range_mid < 0.30:
                    events = []
                    event_bars = {}
                    score = 0
                    
                    if psy_idx:
                        events.append(f"PSY @ ${closes[psy_idx]:.2f}")
                        event_bars["PSY"] = {"idx": psy_idx, "price": closes[psy_idx], "ts": raw_bars[psy_idx]["t"]}
                        score += 10
                    
                    events.append(f"BC @ ${bc_high:.2f} (Vol {bc['vol_ratio']:.1f}x, Close untere Haelfte)")
                    event_bars["BC"] = {"idx": bc_idx, "price": bc_high, "ts": raw_bars[bc_idx]["t"]}
                    score += 20
                    
                    events.append(f"AR @ ${ar_low:.2f}")
                    event_bars["AR"] = {"idx": ar_idx, "price": ar_low, "ts": raw_bars[ar_idx]["t"]}
                    score += 15
                    
                    st_count = 0
                    prev_st_vol = bc["vol_ratio"]
                    for i in range(ar_idx + 3, min(n - 5, ar_idx + int((n - ar_idx) * 0.7))):
                        if highs[i] >= range_high - range_width * 0.25:
                            hi_vol, vr = is_high_volume(i)
                            if vr < prev_st_vol * 0.9:
                                st_label = f"ST{st_count + 1}" if st_count > 0 else "ST"
                                events.append(f"{st_label} @ ${highs[i]:.2f} (Vol {vr:.1f}x)")
                                event_bars[st_label] = {"idx": i, "price": highs[i], "ts": raw_bars[i]["t"]}
                                score += 10 if st_count == 0 else 5
                                prev_st_vol = vr
                                st_count += 1
                                if st_count >= 3:
                                    break
                    
                    # Phase B: Tests nahe SUPPORT (AR-Zone) — Drops TO but holds AT AR
                    # Volume sollte bei Drops zur Support ABNEHMEN
                    st_support_count = 0
                    for i in range(ar_idx + 3, min(n - 5, ar_idx + int((n - ar_idx) * 0.7))):
                        if lows[i] <= range_low + range_width * 0.20:
                            hi_vol, vr = is_high_volume(i)
                            if not hi_vol or vr < 1.3:
                                st_s_label = f"ST-S{st_support_count + 1}" if st_support_count > 0 else "ST-S"
                                events.append(f"{st_s_label} @ ${lows[i]:.2f} (Support Test, Vol {vr:.1f}x)")
                                event_bars[st_s_label] = {"idx": i, "price": lows[i], "ts": raw_bars[i]["t"]}
                                score += 5
                                st_support_count += 1
                                if st_support_count >= 2:
                                    break
                    
                    # UTAD: Break ueber BC-High auf LOW Volume -> Failure
                    utad_idx = None
                    utad_start = max(ar_idx + 5, int(bc_idx + (n - bc_idx) * 0.3))
                    for i in range(utad_start, n - 3):
                        if highs[i] > range_high:
                            low_vol, lvr = is_low_volume(i)
                            if low_vol or lvr < 0.85:
                                for j in range(1, min(6, n - i)):
                                    if closes[i + j] < range_high - range_width * 0.10:
                                        utad_idx = i
                                        events.append(f"UTAD @ ${highs[i]:.2f} (Vol {lvr:.1f}x LOW)")
                                        event_bars["UTAD"] = {"idx": i, "price": highs[i], "ts": raw_bars[i]["t"]}
                                        score += 25
                                        break
                            break
                    
                    # SOW: Wide Spread DOWN + HIGH Volume unter AR-Level
                    sow_idx = None
                    for i in range(max(ar_idx + 10, n - int(n * 0.4)), n):
                        if closes[i] < range_low and closes[i] < opens[i]:
                            hi_vol, vr = is_high_volume(i)
                            if hi_vol and is_wide_spread(i):
                                sow_idx = i
                                events.append(f"SOW @ ${closes[i]:.2f} (Vol {vr:.1f}x, Wide Spread)")
                                event_bars["SOW"] = {"idx": i, "price": closes[i], "ts": raw_bars[i]["t"]}
                                score += 20
                                break
                    
                    if not sow_idx and current_price < range_low:
                        events.append(f"SOW (schwach): ${current_price:.2f} < Range ${range_low:.2f}")
                        score += 8
                    
                    # LPSY: Rally auf abnehmendem Volume, scheitert unter Range Low
                    if sow_idx and sow_idx + 3 < n:
                        for i in range(sow_idx + 1, n):
                            if highs[i] > closes[sow_idx]:
                                low_vol, lvr = is_low_volume(i)
                                if highs[i] <= range_low + range_width * 0.10:
                                    if low_vol or lvr < 0.9:
                                        events.append(f"LPSY @ ${highs[i]:.2f} (Vol {lvr:.1f}x)")
                                        event_bars["LPSY"] = {"idx": i, "price": highs[i], "ts": raw_bars[i]["t"]}
                                        score += 15
                                        break
                    
                    if score >= 35:
                        if sow_idx or current_price < range_low * 0.98:
                            phase = "Phase D/E — Markdown beginning"
                            phase_short = "D/E"
                        elif utad_idx:
                            phase = "Phase C — UTAD (Failed Breakout)"
                            phase_short = "C"
                        elif st_count >= 1:
                            phase = "Phase B — Cause Building"
                            phase_short = "B"
                        else:
                            phase = "Phase A — Buying Exhaustion"
                            phase_short = "A"
                        
                        if utad_idx:
                            entry = range_low
                            stop = highs[utad_idx] + atr * 0.3
                        else:
                            entry = range_low
                            stop = range_high + atr * 0.5
                        
                        tp1 = range_low - range_width * 0.75
                        tp2 = range_low - range_width * 1.5
                        rr = abs(entry - tp1) / abs(stop - entry) if abs(stop - entry) > 0 else 0
                        
                        results.append({
                            "type": "Distribution", "direction": "SHORT",
                            "phase": phase, "phase_short": phase_short,
                            "score": min(score, 100), "events": events, "event_bars": event_bars,
                            "range_high": round(range_high, 2), "range_low": round(range_low, 2),
                            "range_start_ts": raw_bars[bc_idx]["t"],
                            "range_end_ts": raw_bars[min(n-1, ar_idx + (n - ar_idx) // 2)]["t"],
                            "entry": round(entry, 2), "stop": round(stop, 2),
                            "tp1": round(tp1, 2), "tp2": round(tp2, 2),
                            "rr": round(rr, 2), "current_price": round(current_price, 2),
                        })
        
        if not results:
            return None
        best = max(results, key=lambda x: x["score"])
        best["ticker"] = ticker
        return best
    except Exception:
        return None


# ── scan_wyckoff_batch (originally line 5254) ──
def scan_wyckoff_batch(tickers, api_key, days=180, timeframe="hour", direction="LONG"):
    """Scannt mehrere Aktien nach Wyckoff Patterns."""
    results = []
    for i, ticker in enumerate(tickers):
        try:
            result = scan_wyckoff_single(ticker, api_key, days, timeframe)
            if result and (direction == "ALL" or result["direction"] == direction):
                results.append(result)
        except Exception:
            continue
        # Polygon Starter: ~100 Calls/Min → 0.15s zwischen Calls
        if i % 10 == 9:
            time.sleep(1.5)  # 10 Calls in ~1.5s + 1.5s Pause = ~80/Min
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ── detect_volume_imbalances (originally line 9744) ──
def detect_volume_imbalances(ohlcv_data, max_zones=50):
    """
    Erkennt Volume Imbalances (VI), Fair Value Gaps (FVG), und Opening Gaps (OG)
    in OHLCV-Daten. Tracked ob Zonen gefüllt (mitigated) wurden.
    
    ICT DEFINITIONEN (korrekt):
      VI  = Body-to-Body Gap zwischen 2 aufeinanderfolgenden Kerzen (Wicks dürfen überlappen)
      FVG = 3-Kerzen-Pattern: Gap zwischen High[Kerze 1] und Low[Kerze 3] (wick-basiert)
      OG  = Wick-to-Wick Gap — Wicks überlappen sich GAR NICHT (stärkstes Signal)
    
    WICHTIG: VI und FVG werden GETRENNT erkannt (verschiedene Konzepte).
    FVG kann OHNE Body-Gap existieren (große Impulse-Kerze in der Mitte).
    
    Args:
        ohlcv_data: Liste von dicts mit keys: open, high, low, close, volume, time
        max_zones: Maximale Anzahl an Zonen die getrackt werden
    
    Returns:
        dict mit zones, unfilled_bull, unfilled_bear, nearest_bull, nearest_bear, stats
    """
    empty_result = {"zones": [], "unfilled_bull": [], "unfilled_bear": [],
                    "nearest_bull": None, "nearest_bear": None,
                    "stats": {"total": 0, "filled": 0, "unfilled": 0, "fill_rate": 0,
                              "bull_unfilled": 0, "bear_unfilled": 0}}
    
    if not ohlcv_data or len(ohlcv_data) < 5:
        return empty_result
    
    zones = []
    n = len(ohlcv_data)
    current_price = ohlcv_data[-1]["close"]
    
    # Durchschnittliche Range und Volume für Filter
    ranges = [d["high"] - d["low"] for d in ohlcv_data if d["high"] > d["low"]]
    avg_range = sum(ranges) / len(ranges) if ranges else current_price * 0.01
    # 30% der durchschnittlichen Kerzen-Range als Mindestgröße
    # Filtert Mikro-Gaps raus die in jedem Trend entstehen
    min_gap_size = avg_range * 0.30
    
    volumes = [d.get("volume", 0) for d in ohlcv_data]
    avg_vol = sum(volumes) / len(volumes) if volumes else 1
    
    # ================================================================
    # PASS 1a: Volume Imbalances (VI) — 2-Kerzen Body-to-Body Gap
    # Bedingung: Gap zwischen Bodies + Volume der Impulse-Kerze >= 1.0x avg
    # ================================================================
    
    for i in range(1, n):
        c_prev = ohlcv_data[i - 1]
        c_curr = ohlcv_data[i]
        
        # Body-Grenzen (max/min für Doji-Safe)
        body_top_1 = max(c_prev["open"], c_prev["close"])
        body_bot_1 = min(c_prev["open"], c_prev["close"])
        body_top_2 = max(c_curr["open"], c_curr["close"])
        body_bot_2 = min(c_curr["open"], c_curr["close"])
        
        # Volume-Filter: Impulse-Kerze muss mindestens durchschnittliches Volume haben
        impulse_vol = c_curr.get("volume", 0)
        if avg_vol > 0 and impulse_vol < avg_vol * 1.0:
            continue  # Kein institutionelles Interesse
        vol_ratio = impulse_vol / avg_vol if avg_vol > 0 else 1
        
        # --- BULLISH VI: Body Kerze 2 startet ÜBER Body Kerze 1 ---
        if body_bot_2 > body_top_1 + min_gap_size:
            gap_low = body_top_1
            gap_high = body_bot_2
            gap_size = gap_high - gap_low
            gap_mid = (gap_high + gap_low) / 2
            gap_pct = (gap_size / gap_low * 100) if gap_low > 0 else 0
            
            # OG Check: Wicks überlappen sich gar nicht?
            wicks_no_overlap = c_prev["high"] < c_curr["low"]
            zone_type = "OG" if wicks_no_overlap else "VI"
            strength = 3 if wicks_no_overlap else 1
            if vol_ratio > 2.0:
                strength += 1
            
            zones.append({
                "direction": "bullish", "type": zone_type,
                "zone_high": round(gap_high, 4), "zone_low": round(gap_low, 4),
                "zone_mid": round(gap_mid, 4), "gap_pct": round(gap_pct, 2),
                "bar_idx": i, "time": c_curr.get("time", 0),
                "vol_ratio": round(vol_ratio, 1), "strength": strength,
                "filled": False, "ce_filled": False, "fill_bar": None,
            })
        
        # --- BEARISH VI: Body Kerze 2 endet UNTER Body Kerze 1 ---
        elif body_top_2 < body_bot_1 - min_gap_size:
            gap_high = body_bot_1
            gap_low = body_top_2
            gap_size = gap_high - gap_low
            gap_mid = (gap_high + gap_low) / 2
            gap_pct = (gap_size / gap_high * 100) if gap_high > 0 else 0
            
            wicks_no_overlap = c_curr["high"] < c_prev["low"]
            zone_type = "OG" if wicks_no_overlap else "VI"
            strength = 3 if wicks_no_overlap else 1
            if vol_ratio > 2.0:
                strength += 1
            
            zones.append({
                "direction": "bearish", "type": zone_type,
                "zone_high": round(gap_high, 4), "zone_low": round(gap_low, 4),
                "zone_mid": round(gap_mid, 4), "gap_pct": round(gap_pct, 2),
                "bar_idx": i, "time": c_curr.get("time", 0),
                "vol_ratio": round(vol_ratio, 1), "strength": strength,
                "filled": False, "ce_filled": False, "fill_bar": None,
            })
    
    # ================================================================
    # PASS 1b: Fair Value Gaps (FVG) — 3-Kerzen Wick-Gap (SEPARAT)
    # FVG Zone = High[Kerze 1] bis Low[Kerze 3] (bullish)
    #          = Low[Kerze 1] bis High[Kerze 3] (bearish)
    # UNTERSCHIED zu VI: Wick-basiert, kann ohne Body-Gap existieren!
    # ================================================================
    
    for i in range(2, n):
        c1 = ohlcv_data[i - 2]  # Kerze 1
        c2 = ohlcv_data[i - 1]  # Kerze 2 (Impulse)
        c3 = ohlcv_data[i]      # Kerze 3
        
        # Volume der Impulse-Kerze (Kerze 2)
        impulse_vol = c2.get("volume", 0)
        if avg_vol > 0 and impulse_vol < avg_vol * 1.0:
            continue
        vol_ratio = impulse_vol / avg_vol if avg_vol > 0 else 1
        
        # --- BULLISH FVG: High[K1] < Low[K3] ---
        # Wick von Kerze 1 berührt NICHT Wick von Kerze 3
        if c1["high"] < c3["low"] - min_gap_size:
            gap_low = c1["high"]     # Wick-High Kerze 1
            gap_high = c3["low"]     # Wick-Low Kerze 3
            gap_size = gap_high - gap_low
            gap_mid = (gap_high + gap_low) / 2
            gap_pct = (gap_size / gap_low * 100) if gap_low > 0 else 0
            
            strength = 2
            if vol_ratio > 2.0:
                strength += 1
            # Größere FVGs sind stärker
            if gap_pct > 1.0:
                strength += 1
            
            # Prüfe ob diese Zone nicht schon als VI/OG existiert (Deduplizierung)
            already_exists = False
            for z in zones:
                if (z["bar_idx"] in [i, i-1] and z["direction"] == "bullish" and
                    abs(z["zone_low"] - gap_low) < gap_size * 0.5):
                    already_exists = True
                    # Upgrade zu FVG wenn stärker
                    if strength > z["strength"]:
                        z["type"] = "FVG"
                        z["zone_high"] = round(gap_high, 4)
                        z["zone_low"] = round(gap_low, 4)
                        z["zone_mid"] = round(gap_mid, 4)
                        z["gap_pct"] = round(gap_pct, 2)
                        z["strength"] = strength
                    break
            
            if not already_exists:
                zones.append({
                    "direction": "bullish", "type": "FVG",
                    "zone_high": round(gap_high, 4), "zone_low": round(gap_low, 4),
                    "zone_mid": round(gap_mid, 4), "gap_pct": round(gap_pct, 2),
                    "bar_idx": i, "time": c3.get("time", 0),
                    "vol_ratio": round(vol_ratio, 1), "strength": strength,
                    "filled": False, "ce_filled": False, "fill_bar": None,
                })
        
        # --- BEARISH FVG: Low[K1] > High[K3] ---
        if c1["low"] > c3["high"] + min_gap_size:
            gap_high = c1["low"]     # Wick-Low Kerze 1
            gap_low = c3["high"]     # Wick-High Kerze 3
            gap_size = gap_high - gap_low
            gap_mid = (gap_high + gap_low) / 2
            gap_pct = (gap_size / gap_high * 100) if gap_high > 0 else 0
            
            strength = 2
            if vol_ratio > 2.0:
                strength += 1
            if gap_pct > 1.0:
                strength += 1
            
            already_exists = False
            for z in zones:
                if (z["bar_idx"] in [i, i-1] and z["direction"] == "bearish" and
                    abs(z["zone_high"] - gap_high) < gap_size * 0.5):
                    already_exists = True
                    if strength > z["strength"]:
                        z["type"] = "FVG"
                        z["zone_high"] = round(gap_high, 4)
                        z["zone_low"] = round(gap_low, 4)
                        z["zone_mid"] = round(gap_mid, 4)
                        z["gap_pct"] = round(gap_pct, 2)
                        z["strength"] = strength
                    break
            
            if not already_exists:
                zones.append({
                    "direction": "bearish", "type": "FVG",
                    "zone_high": round(gap_high, 4), "zone_low": round(gap_low, 4),
                    "zone_mid": round(gap_mid, 4), "gap_pct": round(gap_pct, 2),
                    "bar_idx": i, "time": c3.get("time", 0),
                    "vol_ratio": round(vol_ratio, 1), "strength": strength,
                    "filled": False, "ce_filled": False, "fill_bar": None,
                })
    
    # Sortiere nach bar_idx für korrektes Mitigation-Tracking
    zones.sort(key=lambda z: z["bar_idx"])
    
    # ================================================================
    # PASS 2: Mitigation — wurde die Zone vom Preis gefüllt?
    # ================================================================
    
    for zone in zones:
        zone_created = zone["bar_idx"]
        zh = zone["zone_high"]
        zl = zone["zone_low"]
        zm = zone["zone_mid"]
        
        for j in range(zone_created + 1, n):
            bar = ohlcv_data[j]
            
            if zone["direction"] == "bullish":
                # Bullish Zone: Preis muss RUNTER in die Zone fallen
                if bar["low"] <= zh:  # Preis hat Zone betreten
                    if bar["low"] <= zm:
                        zone["ce_filled"] = True  # 50% CE
                    if bar["low"] <= zl:
                        zone["filled"] = True  # Komplett gefüllt
                        zone["fill_bar"] = j
                        break
            else:
                # Bearish Zone: Preis muss HOCH in die Zone steigen
                if bar["high"] >= zl:  # Preis hat Zone betreten
                    if bar["high"] >= zm:
                        zone["ce_filled"] = True
                    if bar["high"] >= zh:
                        zone["filled"] = True
                        zone["fill_bar"] = j
                        break
    
    # ================================================================
    # PASS 3: Sortiere und klassifiziere
    # ================================================================
    
    zones = zones[-max_zones:]
    zones.reverse()  # Neueste zuerst
    
    unfilled_bull = [z for z in zones if not z["filled"] and z["direction"] == "bullish" and z["zone_high"] < current_price]
    unfilled_bear = [z for z in zones if not z["filled"] and z["direction"] == "bearish" and z["zone_low"] > current_price]
    
    unfilled_bull.sort(key=lambda z: current_price - z["zone_high"])
    unfilled_bear.sort(key=lambda z: z["zone_low"] - current_price)
    
    nearest_bull = unfilled_bull[0] if unfilled_bull else None
    nearest_bear = unfilled_bear[0] if unfilled_bear else None
    
    total = len(zones)
    filled = sum(1 for z in zones if z["filled"])
    unfilled = total - filled
    fill_rate = round(filled / total * 100, 1) if total > 0 else 0
    
    return {
        "zones": zones,
        "unfilled_bull": unfilled_bull,
        "unfilled_bear": unfilled_bear,
        "nearest_bull": nearest_bull,
        "nearest_bear": nearest_bear,
        "stats": {
            "total": total, "filled": filled, "unfilled": unfilled,
            "fill_rate": fill_rate,
            "bull_unfilled": len(unfilled_bull),
            "bear_unfilled": len(unfilled_bear),
        }
    }


# ── detect_order_blocks (originally line 10065) ──
def detect_order_blocks(ohlcv_data, max_blocks=10):
    """
    Erkennt Bullish/Bearish Order Blocks (nur Aktien).
    
    V68 AUDIT FIXES:
    - Mitigation = Body durchbricht OB Zone (nicht nur Wick-Touch)
    - Displacement = c1+c2 zusammen geprüft (Doji + starke Kerze wird erkannt)
    - OB Zone = Body + optionale Wick-Extension
    """
    empty = {"bullish_obs": [], "bearish_obs": [],
             "nearest_bull_ob": None, "nearest_bear_ob": None}
    if not ohlcv_data or len(ohlcv_data) < 10:
        return empty

    n = len(ohlcv_data)
    current_price = ohlcv_data[-1]["close"]
    ranges = [d["high"] - d["low"] for d in ohlcv_data if d["high"] > d["low"]]
    atr = sum(ranges) / len(ranges) if ranges else current_price * 0.02
    avg_vol = sum(d.get("volume", 0) for d in ohlcv_data) / n if n > 0 else 1
    bullish_obs = []
    bearish_obs = []

    for i in range(1, n - 2):
        c0 = ohlcv_data[i]
        c1 = ohlcv_data[i + 1]
        c2 = ohlcv_data[i + 2] if i + 2 < n else None
        # Validate that c2 exists and is not a duplicate/look-ahead
        if c2 is None:
            continue
        c0_body = c0["close"] - c0["open"]

        # ── BULLISH OB: Bärische Kerze → Displacement nach oben ──
        if c0_body < 0:
            # Fix 2: Displacement = max(c1, c2) Close vs OB Low — egal ob c1 bullisch
            disp_close = max(c1["close"], c2["close"])
            impulse_up = disp_close - c0["low"] if disp_close > c0["high"] else 0
            
            if impulse_up > atr * 1.5:
                ob_high = c0["open"]   # Body High (Open bei bärischer Kerze)
                ob_low = c0["close"]   # Body Low (Close bei bärischer Kerze)
                ob_wick_low = c0["low"]  # Erweiterter Bereich inkl. Wick
                
                strength = 1
                if impulse_up > atr * 2.5: strength += 1
                if impulse_up > atr * 4.0: strength += 1
                # Volume der Displacement-Kerze (die stärkere von c1/c2)
                disp_vol = max(c1.get("volume", 0), c2.get("volume", 0))
                vol_ratio = disp_vol / avg_vol if avg_vol > 0 else 0
                if vol_ratio > 1.5: strength += 1
                if vol_ratio > 2.5: strength += 1
                strength = min(5, strength)
                
                # Fix 1: Mitigation = BODY einer nachfolgenden Kerze durchbricht OB Zone
                # Wick-Touch allein mitigiert NICHT (Wick = Ablehnung)
                mitigated = False
                for j in range(i + 2, n):
                    candle_body_low = min(ohlcv_data[j]["open"], ohlcv_data[j]["close"])
                    if candle_body_low < ob_low:  # Body geht UNTER den OB
                        mitigated = True
                        break
                
                if not mitigated and ob_low < current_price:
                    dist_pct = (current_price - ob_high) / current_price * 100
                    bullish_obs.append({
                        "type": "Bullish OB", "ob_high": round(ob_high, 4),
                        "ob_low": round(ob_low, 4), "ob_wick_low": round(ob_wick_low, 4),
                        "ob_mid": round((ob_high + ob_low) / 2, 4),
                        "impulse_size": round(impulse_up / atr, 1),
                        "vol_ratio": round(vol_ratio, 1), "strength": strength,
                        "mitigated": mitigated, "dist_pct": round(dist_pct, 2),
                        "idx": i, "time": c0.get("time"),
                    })

        # ── BEARISH OB: Bullische Kerze → Displacement nach unten ──
        if c0_body > 0:
            disp_close = min(c1["close"], c2["close"])
            impulse_down = c0["high"] - disp_close if disp_close < c0["low"] else 0
            
            if impulse_down > atr * 1.5:
                ob_high = c0["close"]   # Body High (Close bei bullischer Kerze)
                ob_low = c0["open"]     # Body Low (Open bei bullischer Kerze)
                ob_wick_high = c0["high"]
                
                strength = 1
                if impulse_down > atr * 2.5: strength += 1
                if impulse_down > atr * 4.0: strength += 1
                disp_vol = max(c1.get("volume", 0), c2.get("volume", 0))
                vol_ratio = disp_vol / avg_vol if avg_vol > 0 else 1
                if vol_ratio > 1.5: strength += 1
                if vol_ratio > 2.5: strength += 1
                strength = min(5, strength)
                
                mitigated = False
                for j in range(i + 2, n):
                    candle_body_high = max(ohlcv_data[j]["open"], ohlcv_data[j]["close"])
                    if candle_body_high > ob_high:
                        mitigated = True
                        break
                
                if not mitigated and ob_high > current_price:
                    dist_pct = (ob_low - current_price) / current_price * 100
                    bearish_obs.append({
                        "type": "Bearish OB", "ob_high": round(ob_high, 4),
                        "ob_low": round(ob_low, 4), "ob_wick_high": round(ob_wick_high, 4),
                        "ob_mid": round((ob_high + ob_low) / 2, 4),
                        "impulse_size": round(impulse_down / atr, 1),
                        "vol_ratio": round(vol_ratio, 1), "strength": strength,
                        "mitigated": mitigated, "dist_pct": round(dist_pct, 2),
                        "idx": i, "time": c0.get("time"),
                    })

    bullish_obs.sort(key=lambda x: abs(x["dist_pct"]))
    bearish_obs.sort(key=lambda x: abs(x["dist_pct"]))
    return {
        "bullish_obs": bullish_obs[:max_blocks], "bearish_obs": bearish_obs[:max_blocks],
        "nearest_bull_ob": bullish_obs[0] if bullish_obs else None,
        "nearest_bear_ob": bearish_obs[0] if bearish_obs else None,
    }


# ── detect_liquidity_levels (originally line 10189) ──
def detect_liquidity_levels(ohlcv_data, max_levels=8):
    """
    Erkennt Buyside/Sellside Liquidity Levels (nur Aktien).
    
    V68 AUDIT FIXES:
    - Toleranz ATR-basiert (15% der ATR statt preis-basiert)
    - Nur Equal Highs/Lows (2+ touches) = echte Liquiditätspools
    - Einzelne Swing Points separat als schwächere "Swing" Levels
    """
    empty = {"buyside": [], "sellside": [],
             "nearest_buyside": None, "nearest_sellside": None}
    if not ohlcv_data or len(ohlcv_data) < 15:
        return empty

    n = len(ohlcv_data)
    current_price = ohlcv_data[-1]["close"]
    
    # Fix 4: ATR-basierte Toleranz
    ranges = [d["high"] - d["low"] for d in ohlcv_data if d["high"] > d["low"]]
    atr = sum(ranges) / len(ranges) if ranges else current_price * 0.02
    tol = atr * 0.15  # 15% der ATR
    
    highs = [d["high"] for d in ohlcv_data]
    lows = [d["low"] for d in ohlcv_data]

    # Swing Highs & Lows (3-bar Pivot)
    sw = 3
    swing_highs = []
    swing_lows = []
    for i in range(sw, n - sw):
        if highs[i] >= max(highs[i-sw:i]) and highs[i] >= max(highs[i+1:i+sw+1]):
            swing_highs.append({"price": highs[i], "idx": i, "time": ohlcv_data[i].get("time")})
        if lows[i] <= min(lows[i-sw:i]) and lows[i] <= min(lows[i+1:i+sw+1]):
            swing_lows.append({"price": lows[i], "idx": i, "time": ohlcv_data[i].get("time")})

    # ── Equal Highs → Buyside Liquidity ──
    buyside = []
    used = set()
    for i, sh in enumerate(swing_highs):
        if i in used: continue
        cluster = [sh]; used.add(i)
        for j, sh2 in enumerate(swing_highs):
            if j in used: continue
            if abs(sh["price"] - sh2["price"]) <= tol:
                cluster.append(sh2); used.add(j)
        
        # Fix 5: Nur Equal Highs (2+ touches) = echte Liquidität
        if len(cluster) >= 2:
            max_p = max(c["price"] for c in cluster)
            if max_p > current_price:
                dist_pct = (max_p - current_price) / current_price * 100
                if dist_pct < 10:  # Max 10% Entfernung
                    buyside.append({
                        "type": "Equal Highs", "level": round(max_p, 4),
                        "touches": len(cluster), "strength": min(5, len(cluster)),
                        "dist_pct": round(dist_pct, 2),
                        "label": f"BSL ${max_p:.2f} ({len(cluster)}x)",
                    })

    # ── Equal Lows → Sellside Liquidity ──
    sellside = []
    used = set()
    for i, sl in enumerate(swing_lows):
        if i in used: continue
        cluster = [sl]; used.add(i)
        for j, sl2 in enumerate(swing_lows):
            if j in used: continue
            if abs(sl["price"] - sl2["price"]) <= tol:
                cluster.append(sl2); used.add(j)
        
        if len(cluster) >= 2:
            min_p = min(c["price"] for c in cluster)
            if min_p < current_price:
                dist_pct = (current_price - min_p) / current_price * 100
                if dist_pct < 10:
                    sellside.append({
                        "type": "Equal Lows", "level": round(min_p, 4),
                        "touches": len(cluster), "strength": min(5, len(cluster)),
                        "dist_pct": round(dist_pct, 2),
                        "label": f"SSL ${min_p:.2f} ({len(cluster)}x)",
                    })

    buyside.sort(key=lambda x: x["dist_pct"])
    sellside.sort(key=lambda x: x["dist_pct"])
    return {
        "buyside": buyside[:max_levels], "sellside": sellside[:max_levels],
        "nearest_buyside": buyside[0] if buyside else None,
        "nearest_sellside": sellside[0] if sellside else None,
    }


# ── format_smc_setup (originally line 10280) ──
def format_smc_setup(vi_result, ob_result, liq_result, current_price, ohlcv_data=None):
    """
    Kombiniert FVG + OB + Liquidity zu SMC Trade Setups.
    
    V68 AUDIT FIXES:
    - Fix 6:  R:R = (target - entry) / (entry - stop) mit echtem Entry/Stop
    - Fix 7:  Stop = OB_wick_low - ATR*0.3 (nicht 0.5% pauschal)
    - Fix 8:  Gibt BEIDE Setups zurück (Long + Short)
    - Fix 9:  Entry = OB Zone wenn OB in FVG, sonst FVG High/Low
    - Fix 10: Market Structure Check (HH/HL für Long, LH/LL für Short)
    
    Returns:
        dict mit long_setup, short_setup (beide können has_setup=True/False sein)
    """
    empty_setup = {"has_setup": False, "direction": None, "entry_zone": None,
                   "stop": None, "target": None, "confluence": [], "score": 0, "description": ""}
    result = {"long_setup": dict(empty_setup), "short_setup": dict(empty_setup)}
    
    if not all([vi_result, ob_result, liq_result]):
        return result
    
    # ATR für Stop-Buffer
    atr = 0
    if ohlcv_data and len(ohlcv_data) >= 10:
        ranges = [d["high"] - d["low"] for d in ohlcv_data if d["high"] > d["low"]]
        atr = sum(ranges) / len(ranges) if ranges else current_price * 0.02
    else:
        atr = current_price * 0.02  # Fallback 2%
    
    # ── Fix 10: Market Structure Check ──
    ms_bullish = True   # Default: beide erlaubt
    ms_bearish = True
    if ohlcv_data and len(ohlcv_data) >= 20:
        # Letzte 2 Swing Highs + 2 Swing Lows prüfen
        sw = 3
        n = len(ohlcv_data)
        highs = [d["high"] for d in ohlcv_data]
        lows = [d["low"] for d in ohlcv_data]
        recent_sh = []
        recent_sl = []
        for i in range(sw, n - sw):
            if highs[i] >= max(highs[i-sw:i]) and highs[i] >= max(highs[i+1:i+sw+1]):
                recent_sh.append(highs[i])
            if lows[i] <= min(lows[i-sw:i]) and lows[i] <= min(lows[i+1:i+sw+1]):
                recent_sl.append(lows[i])
        
        if len(recent_sh) >= 2 and len(recent_sl) >= 2:
            last_2_highs = recent_sh[-2:]
            last_2_lows = recent_sl[-2:]
            # Bullisch: Higher Highs + Higher Lows
            ms_bullish = (last_2_highs[-1] >= last_2_highs[-2] * 0.998 and 
                         last_2_lows[-1] >= last_2_lows[-2] * 0.998)
            # Bearisch: Lower Highs + Lower Lows
            ms_bearish = (last_2_highs[-1] <= last_2_highs[-2] * 1.002 and 
                         last_2_lows[-1] <= last_2_lows[-2] * 1.002)
    
    # ── LONG SETUP: Bullish FVG + Bullish OB + BSL Target ──
    if ms_bullish:
        bull_fvgs = vi_result.get("unfilled_bull", [])
        bull_obs = ob_result.get("bullish_obs", [])
        bsl = liq_result.get("nearest_buyside")
        best_long = None
        best_ls = 0
        
        for fvg in bull_fvgs[:5]:
            fh, fl = fvg["zone_high"], fvg["zone_low"]
            dist = (current_price - fh) / current_price * 100 if current_price > 0 else 99
            if dist < 0 or dist > 5:
                continue
            
            s = 20 + fvg["strength"] * 5
            conf = [f" Bullish {fvg['type']} @ ${fl:.2f}-${fh:.2f} ({dist:.1f}% unter Preis)"]
            
            # Fix 9: Suche OB IN der FVG → OB wird Entry-Zone
            ob_in_fvg = None
            for ob in bull_obs:
                if ob["ob_low"] <= fh * 1.005 and ob["ob_high"] >= fl * 0.995:
                    ob_in_fvg = ob
                    s += 25
                    conf.append(f" Bullish OB @ ${ob['ob_low']:.2f}-${ob['ob_high']:.2f} ({ob['impulse_size']:.1f}x ATR)")
                    break
                if ob["ob_high"] <= fl and ob["ob_high"] >= fl * 0.98:
                    ob_in_fvg = ob
                    s += 15
                    conf.append(f" OB nahe FVG @ ${ob['ob_low']:.2f}-${ob['ob_high']:.2f}")
                    break
            
            # Fix 9: Entry = OB wenn vorhanden, sonst FVG
            if ob_in_fvg:
                entry_high = ob_in_fvg["ob_high"]
                entry_low = ob_in_fvg["ob_low"]
                # Fix 7: Stop = OB Wick Low - ATR*0.3 Buffer
                ob_wick = ob_in_fvg.get("ob_wick_low", ob_in_fvg["ob_low"])
                stop_price = ob_wick - atr * 0.3
            else:
                entry_high = fh
                entry_low = fl
                # Fix 7: Stop = FVG Low - ATR*0.3
                stop_price = fl - atr * 0.3
            
            entry_price = entry_high  # Limit Buy am oberen Rand
            
            # Fix 6: Korrekte R:R Berechnung
            risk = entry_price - stop_price
            if bsl and risk > 0:
                reward = bsl["level"] - entry_price
                rr = reward / risk
                s += 15 if rr >= 2.0 else 8 if rr >= 1.5 else 3
                conf.append(f" BSL Target @ ${bsl['level']:.2f} ({bsl['touches']}x, R:R = {rr:.1f})")
            else:
                rr = 0
            
            if s > best_ls:
                best_ls = s
                best_long = {
                    "has_setup": True, "direction": "LONG",
                    "entry_zone": f"${entry_low:.2f} - ${entry_high:.2f}",
                    "entry_price": entry_price,
                    "stop": f"${stop_price:.2f}",
                    "stop_price": stop_price,
                    "target": f"${bsl['level']:.2f}" if bsl else "Nächster Swing High",
                    "rr": round(rr, 1),
                    "confluence": conf,
                    "score": min(100, s),
                }
        
        if best_long:
            best_long["description"] = (
                f" SMC LONG (Score: {best_long['score']}/100, R:R {best_long['rr']})\n"
                f"Entry: {best_long['entry_zone']} | Stop: {best_long['stop']} | TP: {best_long['target']}"
            )
            result["long_setup"] = best_long
    
    # ── SHORT SETUP: Bearish FVG + Bearish OB + SSL Target ──
    if ms_bearish:
        bear_fvgs = vi_result.get("unfilled_bear", [])
        bear_obs = ob_result.get("bearish_obs", [])
        ssl = liq_result.get("nearest_sellside")
        best_short = None
        best_ss = 0
        
        for fvg in bear_fvgs[:5]:
            fh, fl = fvg["zone_high"], fvg["zone_low"]
            dist = (fl - current_price) / current_price * 100 if current_price > 0 else 99
            if dist < 0 or dist > 5:
                continue
            
            s = 20 + fvg["strength"] * 5
            conf = [f" Bearish {fvg['type']} @ ${fl:.2f}-${fh:.2f} ({dist:.1f}% über Preis)"]
            
            ob_in_fvg = None
            for ob in bear_obs:
                if ob["ob_low"] <= fh * 1.005 and ob["ob_high"] >= fl * 0.995:
                    ob_in_fvg = ob
                    s += 25
                    conf.append(f" Bearish OB @ ${ob['ob_low']:.2f}-${ob['ob_high']:.2f} ({ob['impulse_size']:.1f}x ATR)")
                    break
                if ob["ob_low"] >= fh and ob["ob_low"] <= fh * 1.02:
                    ob_in_fvg = ob
                    s += 15
                    conf.append(f" OB nahe FVG @ ${ob['ob_low']:.2f}-${ob['ob_high']:.2f}")
                    break
            
            if ob_in_fvg:
                entry_high = ob_in_fvg["ob_high"]
                entry_low = ob_in_fvg["ob_low"]
                ob_wick = ob_in_fvg.get("ob_wick_high", ob_in_fvg["ob_high"])
                stop_price = ob_wick + atr * 0.3
            else:
                entry_high = fh
                entry_low = fl
                stop_price = fh + atr * 0.3
            
            entry_price = entry_low  # Limit Sell am unteren Rand
            
            risk = stop_price - entry_price
            if ssl and risk > 0:
                reward = entry_price - ssl["level"]
                rr = reward / risk
                s += 15 if rr >= 2.0 else 8 if rr >= 1.5 else 3
                conf.append(f" SSL Target @ ${ssl['level']:.2f} ({ssl['touches']}x, R:R = {rr:.1f})")
            else:
                rr = 0
            
            if s > best_ss:
                best_ss = s
                best_short = {
                    "has_setup": True, "direction": "SHORT",
                    "entry_zone": f"${entry_low:.2f} - ${entry_high:.2f}",
                    "entry_price": entry_price,
                    "stop": f"${stop_price:.2f}",
                    "stop_price": stop_price,
                    "target": f"${ssl['level']:.2f}" if ssl else "Nächster Swing Low",
                    "rr": round(rr, 1),
                    "confluence": conf,
                    "score": min(100, s),
                }
        
        if best_short:
            best_short["description"] = (
                f" SMC SHORT (Score: {best_short['score']}/100, R:R {best_short['rr']})\n"
                f"Entry: {best_short['entry_zone']} | Stop: {best_short['stop']} | TP: {best_short['target']}"
            )
            result["short_setup"] = best_short
    
    return result


# ── detect_wolfe_waves (originally line 10488) ──
def detect_wolfe_waves(ohlcv_data, lookback=80, min_wave_bars=5, max_wave_bars=40):
    """
    Erkennt Wolfe Wave Patterns (Long und Short) in OHLCV-Daten.
    
    WOLFE WAVE REGELN (Bill Wolfe):
    
    BULLISH (Falling Wedge → Long):
      Punkt 1: Swing Low
      Punkt 2: Swing High nach 1
      Punkt 3: Tieferer Low als 1 (definiert untere Trendlinie 1→3)
      Punkt 4: High ZWISCHEN 1 und 2 (bleibt im Kanal)
      Punkt 5: Fällt UNTER die Linie 1→3 (Überschuss = Sweet Spot Entry)
      Target:  Linie 1→4 auf Zeitpunkt von Punkt 5 projiziert
      Stop:    Unter Punkt 5
    
    BEARISH (Rising Wedge → Short):
      Punkt 1: Swing High
      Punkt 2: Swing Low nach 1
      Punkt 3: Höherer High als 1 (definiert obere Trendlinie 1→3)
      Punkt 4: Low ZWISCHEN 1 und 2 (bleibt im Kanal)
      Punkt 5: Steigt ÜBER die Linie 1→3 (Überschuss = Sweet Spot Entry)
      Target:  Linie 1→4 auf Zeitpunkt von Punkt 5 projiziert
      Stop:    Über Punkt 5
    
    GEOMETRIE:
      - Linien 1→3 und 2→4 müssen konvergieren (Wedge-Form)
      - Punkt 5 muss die Linie 1→3 überschreiten
      - Überschuss darf nicht zu groß sein (max 150% der Zone-Höhe)
      - Mindestabstand zwischen Punkten (min_wave_bars)
      - Punkte müssen zeitlich geordnet sein: 1 < 2 < 3 < 4 < 5
    
    Args:
        ohlcv_data: OHLCV-Daten
        lookback: Wie viele Bars zurückschauen
        min_wave_bars: Mindest-Bars zwischen zwei aufeinanderfolgenden Pivots
        max_wave_bars: Max-Bars zwischen zwei aufeinanderfolgenden Pivots
    
    Returns:
        Liste von erkannten Wolfe Waves mit Entry, Stop, Target, Score
    """
    if not ohlcv_data or len(ohlcv_data) < 30:
        return []
    
    data = ohlcv_data[-lookback:] if len(ohlcv_data) > lookback else ohlcv_data
    n = len(data)
    
    highs = [d["high"] for d in data]
    lows = [d["low"] for d in data]
    closes = [d["close"] for d in data]
    volumes = [d.get("volume", 0) for d in data]
    
    current_price = closes[-1]
    avg_range = sum(highs[i] - lows[i] for i in range(n)) / n if n > 0 else 1
    
    # ATR für Stop-Berechnung (14-Perioden)
    atr_values = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        atr_values.append(tr)
    atr = sum(atr_values[-14:]) / min(14, len(atr_values)) if atr_values else avg_range
    
    avg_vol = sum(volumes) / len(volumes) if volumes else 1
    
    # ================================================================
    # SWING POINT DETECTION (mit kleinerem Window für mehr Pivots)
    # ================================================================
    swing_window = max(3, min(6, n // 12))
    
    swing_highs = []
    swing_lows = []
    
    for i in range(swing_window, n - swing_window):
        if highs[i] >= max(highs[i-swing_window:i]) and highs[i] >= max(highs[i+1:i+swing_window+1]):
            swing_highs.append({"price": highs[i], "idx": i, "vol": volumes[i]})
        if lows[i] <= min(lows[i-swing_window:i]) and lows[i] <= min(lows[i+1:i+swing_window+1]):
            swing_lows.append({"price": lows[i], "idx": i, "vol": volumes[i]})
    
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return []
    
    waves = []
    
    # ================================================================
    # Hilfsfunktion: Linie durch 2 Punkte → Y bei gegebenem X (Bar-Index)
    # ================================================================
    def line_y_at(x1, y1, x2, y2, x_target):
        """Berechnet Y-Wert einer Linie durch (x1,y1) und (x2,y2) bei x_target."""
        if x2 == x1:
            return y1
        slope = (y2 - y1) / (x2 - x1)
        return y1 + slope * (x_target - x1)
    
    # ================================================================
    # BULLISH WOLFE WAVE — Falling Wedge Entry Long
    # Punkte: Low1 → High2 → Low3 → High4 → Low5
    # ================================================================
    
    for i1 in range(len(swing_lows)):
        p1 = swing_lows[i1]
        
        # Punkt 2: Nächster Swing High NACH Punkt 1
        for i2 in range(len(swing_highs)):
            p2 = swing_highs[i2]
            if p2["idx"] <= p1["idx"] + min_wave_bars:
                continue
            if p2["idx"] > p1["idx"] + max_wave_bars:
                break
            
            # Punkt 3: Nächster Swing Low NACH Punkt 2, TIEFER als Punkt 1
            for i3 in range(i1 + 1, len(swing_lows)):
                p3 = swing_lows[i3]
                if p3["idx"] <= p2["idx"] + min_wave_bars:
                    continue
                if p3["idx"] > p2["idx"] + max_wave_bars:
                    break
                if p3["price"] >= p1["price"]:
                    continue  # Punkt 3 muss tiefer als Punkt 1 sein
                
                # Punkt 4: Nächster Swing High NACH Punkt 3
                # Muss ZWISCHEN Punkt 1 und 2 liegen (im Kanal)
                for i4 in range(i2 + 1, len(swing_highs)):
                    p4 = swing_highs[i4]
                    if p4["idx"] <= p3["idx"] + min_wave_bars:
                        continue
                    if p4["idx"] > p3["idx"] + max_wave_bars:
                        break
                    
                    # Punkt 4 muss unter Punkt 2 sein (tiefer werdende Highs)
                    if p4["price"] >= p2["price"]:
                        continue
                    
                    # Punkt 4 muss ZWISCHEN den Trendlinien liegen (im Kanal)
                    # Obere Linie (2→?): extrapoliert von P2 in Richtung P4
                    # Untere Linie (1→3): P4 muss darüber liegen
                    line_13_at_p4 = line_y_at(p1["idx"], p1["price"], p3["idx"], p3["price"], p4["idx"])
                    if p4["price"] <= line_13_at_p4:
                        continue  # P4 liegt unter/auf der unteren Trendlinie = nicht im Kanal
                    
                    # Prüfe Konvergenz: Linien 1→3 und 2→4 müssen zusammenlaufen
                    slope_13 = (p3["price"] - p1["price"]) / (p3["idx"] - p1["idx"]) if p3["idx"] != p1["idx"] else 0
                    slope_24 = (p4["price"] - p2["price"]) / (p4["idx"] - p2["idx"]) if p4["idx"] != p2["idx"] else 0
                    
                    # Beide Slopes müssen negativ sein (fallend) UND konvergieren
                    # Untere Linie (1→3) fällt stärker als obere Linie (2→4) → konvergiert
                    if slope_13 >= 0 or slope_24 >= 0:
                        continue  # Nicht fallend
                    if slope_13 >= slope_24:
                        continue  # Nicht konvergierend (untere muss steiler fallen)
                    
                    # Punkt 5: Aktueller Preis oder letzter Swing Low UNTER der Linie 1→3
                    # Suche Punkt 5 NACH Punkt 4
                    p5_candidates = []
                    
                    # Letzter Swing Low nach Punkt 4
                    for i5 in range(i3 + 1, len(swing_lows)):
                        p5c = swing_lows[i5]
                        if p5c["idx"] <= p4["idx"] + min_wave_bars:
                            continue
                        if p5c["idx"] > p4["idx"] + max_wave_bars:
                            break
                        p5_candidates.append(p5c)
                    
                    # Alternativ: Aktueller Preis als Punkt 5 wenn er unter der Linie liegt
                    if n - 1 > p4["idx"] + min_wave_bars:
                        p5_candidates.append({"price": min(lows[-3:]), "idx": n - 2, "vol": volumes[-1]})
                    
                    for p5 in p5_candidates:
                        # Linie 1→3 bei Punkt 5 berechnen
                        line_13_at_5 = line_y_at(p1["idx"], p1["price"], p3["idx"], p3["price"], p5["idx"])
                        
                        # Punkt 5 muss UNTER der Linie 1→3 liegen (Überschuss)
                        overshoot = line_13_at_5 - p5["price"]
                        
                        if overshoot <= 0:
                            continue  # Kein Überschuss = kein Wolfe Wave
                        
                        # Überschuss darf nicht zu groß sein
                        # Max 150% der Kanal-Höhe bei Punkt 5
                        channel_height = abs(line_y_at(p2["idx"], p2["price"], p4["idx"], p4["price"], p5["idx"]) - line_13_at_5)
                        if channel_height <= 0:
                            continue
                        
                        overshoot_pct = overshoot / channel_height
                        if overshoot_pct > 1.5:
                            continue  # Zu großer Überschuss = Pattern gebrochen
                        
                        # Target: Linie 1→4 projiziert auf Punkt 5 Zeit
                        target = line_y_at(p1["idx"], p1["price"], p4["idx"], p4["price"], p5["idx"])
                        
                        # Target muss über Punkt 5 liegen (Profit)
                        if target <= p5["price"]:
                            continue
                        
                        # Score berechnen
                        score = 50
                        
                        # Symmetrie: Wie gleichmäßig sind die Punkte verteilt?
                        wave_bars = p5["idx"] - p1["idx"]
                        spacing_12 = (p2["idx"] - p1["idx"]) / wave_bars if wave_bars > 0 else 0
                        spacing_23 = (p3["idx"] - p2["idx"]) / wave_bars if wave_bars > 0 else 0
                        spacing_34 = (p4["idx"] - p3["idx"]) / wave_bars if wave_bars > 0 else 0
                        spacing_45 = (p5["idx"] - p4["idx"]) / wave_bars if wave_bars > 0 else 0
                        spacings = [spacing_12, spacing_23, spacing_34, spacing_45]
                        avg_spacing = sum(spacings) / 4
                        spacing_deviation = sum(abs(s - avg_spacing) for s in spacings) / 4
                        if spacing_deviation < 0.08:
                            score += 15  # Sehr gleichmäßig
                        elif spacing_deviation < 0.15:
                            score += 8
                        
                        # Konvergenz-Qualität: Je stärker die Konvergenz, desto besser
                        convergence_ratio = abs(slope_13) / abs(slope_24) if slope_24 != 0 else 0
                        if 1.2 <= convergence_ratio <= 3.0:
                            score += 10  # Gute Konvergenz
                        
                        # Überschuss-Qualität: 20-80% = ideal
                        if 0.15 <= overshoot_pct <= 0.80:
                            score += 10  # Idealer Überschuss
                        elif overshoot_pct < 0.15:
                            score -= 5   # Kaum Überschuss
                        
                        # Punkt 5 nahe am aktuellen Preis (aktuell relevant)
                        bars_ago = n - 1 - p5["idx"]
                        if bars_ago <= 3:
                            score += 10  # Frisch — gerade jetzt Entry
                        elif bars_ago <= 8:
                            score += 5   # Noch relevant
                        else:
                            score -= 5   # Schon älter
                        
                        # Volume bei Punkt 5 (niedriger als Durchschnitt = Exhaustion der Seller)
                        if avg_vol > 0 and p5["vol"] < avg_vol * 0.8:
                            score += 5  # Low Volume am Punkt 5 = Seller erschöpft
                        
                        # Volume Reversal Check: Bars NACH P5 zeigen steigendes Volume?
                        if p5["idx"] + 2 < n:
                            post_p5_vols = volumes[p5["idx"]+1:min(p5["idx"]+4, n)]
                            if post_p5_vols and avg_vol > 0:
                                avg_post = sum(post_p5_vols) / len(post_p5_vols)
                                if avg_post > avg_vol * 1.2:
                                    score += 8  # Steigendes Volume nach P5 = Reversal bestätigt
                                elif avg_post > p5["vol"] * 1.3:
                                    score += 5  # Volume nimmt zu vs P5
                        
                        # Stop = P5 - 1.5x ATR (realistischer als Overshoot-basiert)
                        stop_price = p5["price"] - atr * 1.5
                        risk = abs(p5["price"] - stop_price)
                        
                        # Entry = P5 Preis (Pattern zeigt die Zone, Trader wartet auf Bestätigung)
                        # Wir kennzeichnen es als "Entry Zone" nicht exakten Preis
                        entry_price = p5["price"]
                        
                        # Confirmation Level = Linie 1→3 (Close darüber = Bestätigung)
                        confirmation_level = line_13_at_5
                        
                        reward = abs(target - entry_price)
                        rr_ratio = reward / risk if risk > 0 else 0
                        
                        if rr_ratio >= 3.0:
                            score += 10
                        elif rr_ratio >= 2.0:
                            score += 5
                        
                        score = max(0, min(100, score))
                        
                        if score >= 45:
                            waves.append({
                                "direction": "bullish",
                                "pattern": "Wolfe Wave Long",
                                "points": {
                                    "p1": {"price": round(p1["price"], 4), "idx": p1["idx"]},
                                    "p2": {"price": round(p2["price"], 4), "idx": p2["idx"]},
                                    "p3": {"price": round(p3["price"], 4), "idx": p3["idx"]},
                                    "p4": {"price": round(p4["price"], 4), "idx": p4["idx"]},
                                    "p5": {"price": round(p5["price"], 4), "idx": p5["idx"]},
                                },
                                "entry": round(entry_price, 4),
                                "confirmation": round(confirmation_level, 4),
                                "stop": round(stop_price, 4),
                                "target": round(target, 4),
                                "rr_ratio": round(rr_ratio, 1),
                                "overshoot_pct": round(overshoot_pct * 100, 1),
                                "score": score,
                                "confidence": "High" if score >= 75 else "Medium" if score >= 55 else "Low",
                                "bars_ago": bars_ago,
                            })
                            break  # Nur bestes Punkt-5 pro 1-2-3-4 Kombination
    
    # ================================================================
    # BEARISH WOLFE WAVE — Rising Wedge Entry Short
    # Punkte: High1 → Low2 → High3 → Low4 → High5
    # ================================================================
    
    for i1 in range(len(swing_highs)):
        p1 = swing_highs[i1]
        
        # Punkt 2: Nächster Swing Low NACH Punkt 1
        for i2 in range(len(swing_lows)):
            p2 = swing_lows[i2]
            if p2["idx"] <= p1["idx"] + min_wave_bars:
                continue
            if p2["idx"] > p1["idx"] + max_wave_bars:
                break
            
            # Punkt 3: Nächster Swing High NACH Punkt 2, HÖHER als Punkt 1
            for i3 in range(i1 + 1, len(swing_highs)):
                p3 = swing_highs[i3]
                if p3["idx"] <= p2["idx"] + min_wave_bars:
                    continue
                if p3["idx"] > p2["idx"] + max_wave_bars:
                    break
                if p3["price"] <= p1["price"]:
                    continue  # Punkt 3 muss höher als Punkt 1 sein
                
                # Punkt 4: Nächster Swing Low NACH Punkt 3
                # Muss ZWISCHEN Punkt 1 und 2 liegen (im Kanal)
                for i4 in range(i2 + 1, len(swing_lows)):
                    p4 = swing_lows[i4]
                    if p4["idx"] <= p3["idx"] + min_wave_bars:
                        continue
                    if p4["idx"] > p3["idx"] + max_wave_bars:
                        break
                    
                    # Punkt 4 muss über Punkt 2 sein (steigende Lows)
                    if p4["price"] <= p2["price"]:
                        continue
                    
                    # Punkt 4 muss ZWISCHEN den Trendlinien liegen (im Kanal)
                    # Obere Linie (1→3): P4 muss darunter liegen
                    line_13_at_p4 = line_y_at(p1["idx"], p1["price"], p3["idx"], p3["price"], p4["idx"])
                    if p4["price"] >= line_13_at_p4:
                        continue  # P4 liegt über/auf der oberen Trendlinie = nicht im Kanal
                    
                    # Prüfe Konvergenz: Linien 1→3 und 2→4 müssen zusammenlaufen
                    slope_13 = (p3["price"] - p1["price"]) / (p3["idx"] - p1["idx"]) if p3["idx"] != p1["idx"] else 0
                    slope_24 = (p4["price"] - p2["price"]) / (p4["idx"] - p2["idx"]) if p4["idx"] != p2["idx"] else 0
                    
                    # Beide Slopes müssen positiv sein (steigend) UND konvergieren
                    # Obere Linie (1→3) steigt langsamer als untere Linie (2→4) → konvergiert
                    if slope_13 <= 0 or slope_24 <= 0:
                        continue
                    if slope_13 >= slope_24:
                        continue  # Nicht konvergierend (obere muss flacher steigen)
                    
                    # Punkt 5: High ÜBER der Linie 1→3
                    p5_candidates = []
                    
                    for i5 in range(i3 + 1, len(swing_highs)):
                        p5c = swing_highs[i5]
                        if p5c["idx"] <= p4["idx"] + min_wave_bars:
                            continue
                        if p5c["idx"] > p4["idx"] + max_wave_bars:
                            break
                        p5_candidates.append(p5c)
                    
                    if n - 1 > p4["idx"] + min_wave_bars:
                        p5_candidates.append({"price": max(highs[-3:]), "idx": n - 2, "vol": volumes[-1]})
                    
                    for p5 in p5_candidates:
                        # Linie 1→3 bei Punkt 5
                        line_13_at_5 = line_y_at(p1["idx"], p1["price"], p3["idx"], p3["price"], p5["idx"])
                        
                        # Punkt 5 muss ÜBER der Linie 1→3 liegen (Überschuss)
                        overshoot = p5["price"] - line_13_at_5
                        
                        if overshoot <= 0:
                            continue
                        
                        channel_height = abs(line_13_at_5 - line_y_at(p2["idx"], p2["price"], p4["idx"], p4["price"], p5["idx"]))
                        if channel_height <= 0:
                            continue
                        
                        overshoot_pct = overshoot / channel_height
                        if overshoot_pct > 1.5:
                            continue
                        
                        # Target: Linie 1→4 projiziert auf Punkt 5 Zeit
                        target = line_y_at(p1["idx"], p1["price"], p4["idx"], p4["price"], p5["idx"])
                        
                        # Target muss unter Punkt 5 liegen (Profit für Short)
                        if target >= p5["price"]:
                            continue
                        
                        # Score
                        score = 50
                        
                        wave_bars = p5["idx"] - p1["idx"]
                        spacing_12 = (p2["idx"] - p1["idx"]) / wave_bars if wave_bars > 0 else 0
                        spacing_23 = (p3["idx"] - p2["idx"]) / wave_bars if wave_bars > 0 else 0
                        spacing_34 = (p4["idx"] - p3["idx"]) / wave_bars if wave_bars > 0 else 0
                        spacing_45 = (p5["idx"] - p4["idx"]) / wave_bars if wave_bars > 0 else 0
                        spacings = [spacing_12, spacing_23, spacing_34, spacing_45]
                        avg_spacing = sum(spacings) / 4
                        spacing_deviation = sum(abs(s - avg_spacing) for s in spacings) / 4
                        if spacing_deviation < 0.08:
                            score += 15
                        elif spacing_deviation < 0.15:
                            score += 8
                        
                        convergence_ratio = abs(slope_24) / abs(slope_13) if slope_13 != 0 else 0
                        if 1.2 <= convergence_ratio <= 3.0:
                            score += 10
                        
                        if 0.15 <= overshoot_pct <= 0.80:
                            score += 10
                        elif overshoot_pct < 0.15:
                            score -= 5
                        
                        bars_ago = n - 1 - p5["idx"]
                        if bars_ago <= 3:
                            score += 10
                        elif bars_ago <= 8:
                            score += 5
                        else:
                            score -= 5
                        
                        avg_vol_local = avg_vol
                        if avg_vol_local > 0 and p5["vol"] < avg_vol_local * 0.8:
                            score += 5
                        
                        # Volume Reversal Check: Bars NACH P5 zeigen steigendes Volume?
                        if p5["idx"] + 2 < n:
                            post_p5_vols = volumes[p5["idx"]+1:min(p5["idx"]+4, n)]
                            if post_p5_vols and avg_vol_local > 0:
                                avg_post = sum(post_p5_vols) / len(post_p5_vols)
                                if avg_post > avg_vol_local * 1.2:
                                    score += 8
                                elif avg_post > p5["vol"] * 1.3:
                                    score += 5
                        
                        # Stop = P5 + 1.5x ATR (realistisch)
                        stop_price = p5["price"] + atr * 1.5
                        risk = abs(stop_price - p5["price"])
                        
                        entry_price = p5["price"]
                        confirmation_level = line_13_at_5  # Close darunter = Bestätigung
                        
                        reward = abs(entry_price - target)
                        rr_ratio = reward / risk if risk > 0 else 0
                        
                        if rr_ratio >= 3.0:
                            score += 10
                        elif rr_ratio >= 2.0:
                            score += 5
                        
                        score = max(0, min(100, score))
                        
                        if score >= 45:
                            waves.append({
                                "direction": "bearish",
                                "pattern": "Wolfe Wave Short",
                                "points": {
                                    "p1": {"price": round(p1["price"], 4), "idx": p1["idx"]},
                                    "p2": {"price": round(p2["price"], 4), "idx": p2["idx"]},
                                    "p3": {"price": round(p3["price"], 4), "idx": p3["idx"]},
                                    "p4": {"price": round(p4["price"], 4), "idx": p4["idx"]},
                                    "p5": {"price": round(p5["price"], 4), "idx": p5["idx"]},
                                },
                                "entry": round(entry_price, 4),
                                "confirmation": round(confirmation_level, 4),
                                "stop": round(stop_price, 4),
                                "target": round(target, 4),
                                "rr_ratio": round(rr_ratio, 1),
                                "overshoot_pct": round(overshoot_pct * 100, 1),
                                "score": score,
                                "confidence": "High" if score >= 75 else "Medium" if score >= 55 else "Low",
                                "bars_ago": bars_ago,
                            })
                            break
    
    # ================================================================
    # DEDUPLIZIERUNG — Nur das beste Pattern pro Richtung/Zeitraum
    # ================================================================
    # Sortiere nach Score (beste zuerst)
    waves.sort(key=lambda w: w["score"], reverse=True)
    
    # Entferne Überlappungen: Wenn Punkt 5 innerhalb von 5 Bars
    final_waves = []
    for w in waves:
        p5_idx = w["points"]["p5"]["idx"]
        is_duplicate = False
        for fw in final_waves:
            if fw["direction"] == w["direction"] and abs(fw["points"]["p5"]["idx"] - p5_idx) < 5:
                is_duplicate = True
                break
        if not is_duplicate:
            final_waves.append(w)
    
    return final_waves[:4]  # Max 4 Waves (2 bull, 2 bear)


# ── detect_chart_patterns (originally line 10979) ──
def find_harmonic_for_chart(ohlcv_data):
    """
    Findet Harmonic Patterns direkt aus Chart-OHLCV-Daten (jeder Timeframe).
    Returns: Liste von Patterns mit XABCD-Koordinaten für Chart-Rendering
    """
    if not ohlcv_data or len(ohlcv_data) < 20:
        return []
    try:
        prices = []
        for d in ohlcv_data:
            prices.append({
                "date": str(d.get("time", "")),
                "high": d["high"],
                "low": d["low"],
                "close": d["close"],
                "open": d["open"],
                "volume": d.get("volume", 0)
            })
        pivots = find_pivots(prices, window=3)
        if len(pivots) < 5:
            return []
        patterns = identify_harmonic_pattern(pivots, prices)
        if not patterns:
            return []
        chart_patterns = []
        for pat in patterns[:3]:
            points = []
            pivot_indices = pat.get("pivot_indices", [])
            point_labels = ["X", "A", "B", "C", "D"]
            for idx, label in zip(pivot_indices, point_labels):
                if idx < len(ohlcv_data):
                    points.append({
                        "time": ohlcv_data[idx]["time"],
                        "price": pat["points"][label],
                        "label": label
                    })
            if len(points) == 5:
                chart_patterns.append({
                    "pattern": pat["pattern"],
                    "emoji": pat["emoji"],
                    "direction": pat["direction"],
                    "score": pat["score"],
                    "matches": pat["matches"],
                    "points": points,
                    "ratios": pat["ratios"],
                    "trade": pat.get("trade", {}),
                    "success_rate": pat.get("success_rate", 0)
                })
        return chart_patterns
    except Exception as e:
        return []


def detect_chart_patterns(ohlcv_data, lookback=50):
    """
    Erkennt Chart-Patterns automatisch.
    
    V67.1 REWRITE - Strengere Kriterien:
    - Adaptives Swing-Window (min 5, skaliert mit Datenmenge)
    - Mindestabstand zwischen Pattern-Punkten (min 10 Bars)
    - Mindesttiefe/Höhe für Neckline
    - Volume-Bestätigung
    - Weniger "forming" false positives
    
    Returns:
        List of detected patterns with details
    """
    if not ohlcv_data or len(ohlcv_data) < 30:
        return []
    
    patterns = []
    
    try:
        data = ohlcv_data[-lookback:]
        highs = [d["high"] for d in data]
        lows = [d["low"] for d in data]
        closes = [d["close"] for d in data]
        volumes = [d.get("volume", 0) for d in data]
        
        current_price = closes[-1]
        avg_volume = sum(volumes) / len(volumes) if volumes else 1
        
        # ATR-basierte Toleranz (adaptiv statt hardcoded)
        atr_values = []
        for i in range(1, len(data)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            atr_values.append(tr)
        atr = sum(atr_values[-14:]) / min(14, len(atr_values)) if atr_values else current_price * 0.02
        atr_pct = atr / current_price if current_price > 0 else 0.02
        
        # V4.0: Mindestabstand proportional zum Lookback
        # lookback=50 → min_gap=10, lookback=150 → min_gap=25, lookback=200 → min_gap=30
        min_pattern_gap = max(10, lookback // 6)
        min_depth_atr = 2.5 if lookback >= 100 else 2.0  # Strengere Tiefe bei höheren TFs

        # Dual-Pass Swing-Erkennung: Großes + kleines Fenster
        # Pass 1: Adaptives großes Fenster (5-10) für etablierte Swings
        # Pass 2: Fenster=3 für scharfe Spikes (schnelle H&S-Köpfe etc.)
        swing_window = max(5, min(10, len(data) // 8))

        swing_highs = []
        swing_lows = []

        for _sw in [swing_window, 3]:
            for i in range(_sw, len(data) - _sw):
                if highs[i] >= max(highs[i-_sw:i]) and highs[i] >= max(highs[i+1:i+_sw+1]):
                    # Deduplizieren: kein existierender Swing innerhalb ±2 Bars
                    if not any(abs(i - s["index"]) <= 2 for s in swing_highs):
                        swing_highs.append({"price": highs[i], "index": i, "volume": volumes[i]})
                if lows[i] <= min(lows[i-_sw:i]) and lows[i] <= min(lows[i+1:i+_sw+1]):
                    if not any(abs(i - s["index"]) <= 2 for s in swing_lows):
                        swing_lows.append({"price": lows[i], "index": i, "volume": volumes[i]})

        # Nach Index sortieren für korrekte Pattern-Reihenfolge
        swing_highs.sort(key=lambda x: x["index"])
        swing_lows.sort(key=lambda x: x["index"])
        
        # === DOUBLE TOP ===
        if len(swing_highs) >= 2:
            # Prüfe alle Paare, nicht nur die letzten zwei
            for a in range(len(swing_highs) - 1):
                for b in range(a + 1, len(swing_highs)):
                    h1_data = swing_highs[a]
                    h2_data = swing_highs[b]
                    h1, h2 = h1_data["price"], h2_data["price"]
                    idx1, idx2 = h1_data["index"], h2_data["index"]
                    
                    # KRITERIUM 1: Mindestabstand zwischen Tops (min 10 Bars)
                    if idx2 - idx1 < min_pattern_gap:
                        continue
                    
                    # KRITERIUM 2: Ähnliche Höhe (innerhalb 1.5× ATR)
                    if abs(h1 - h2) > atr * 1.5:
                        continue
                    
                    # KRITERIUM 3: Neckline muss signifikant tiefer sein (min 2× ATR)
                    neckline = min(lows[idx1:idx2+1])
                    top_avg = (h1 + h2) / 2
                    depth = top_avg - neckline
                    
                    if depth < atr * min_depth_atr:
                        continue
                    
                    # KRITERIUM 4: VORHERIGER UPTREND
                    # Der Kurs VOR dem ersten Top muss tiefer gewesen sein
                    pre_start = max(0, idx1 - 15)
                    pre_end = max(0, idx1 - 2)
                    if pre_end > pre_start:
                        pre_lows = lows[pre_start:pre_end]
                        pre_low_avg = sum(pre_lows) / len(pre_lows)
                        # Vorheriges Level muss deutlich tiefer sein als die Tops
                        if top_avg - pre_low_avg < atr * 2:
                            continue
                    
                    # KRITERIUM 5: KLARER DIP zwischen den Tops
                    mid_section = closes[idx1:idx2+1]
                    if mid_section:
                        mid_avg = sum(mid_section) / len(mid_section)
                        # Wenn der Durchschnitt zwischen Tops nahe den Tops liegt → Range, kein M-Pattern
                        if top_avg - mid_avg < depth * 0.3:
                            continue
                    
                    # KRITERIUM 6: RECENCY
                    if idx2 < len(data) * 0.3:
                        continue
                    
                    # KRITERIUM 7: Volume-Divergenz (weniger Vol beim 2. Top)
                    vol_confirmation = h2_data["volume"] < h1_data["volume"] * 1.2
                    
                    # Preis unter Neckline = bestätigt
                    if current_price < neckline:
                        neckline_idx = idx1 + lows[idx1:idx2+1].index(min(lows[idx1:idx2+1]))
                        patterns.append({
                            "pattern": "Double Top",
                            "emoji": "",
                            "type": "bearish",
                            "level1": round(h1, 2),
                            "level2": round(h2, 2),
                            "neckline": round(neckline, 2),
                            "target": round(neckline - depth, 2),
                            "confidence": "High" if vol_confirmation else "Medium",
                            "description": f"Double Top @ ${top_avg:.2f} - Neckline ${neckline:.2f} broken. Target: ${neckline - depth:.2f}",
                            "draw_points": [{"index": idx1, "price": h1}, {"index": neckline_idx, "price": neckline}, {"index": idx2, "price": h2}]
                        })
                        break  # Nur das beste Pattern nehmen
                    elif current_price < top_avg * 0.97 and current_price > neckline:
                        neckline_idx = idx1 + lows[idx1:idx2+1].index(min(lows[idx1:idx2+1]))
                        patterns.append({
                            "pattern": "Double Top (forming)",
                            "emoji": "",
                            "type": "bearish",
                            "level1": round(h1, 2),
                            "level2": round(h2, 2),
                            "neckline": round(neckline, 2),
                            "target": round(neckline - depth, 2),
                            "confidence": "Low",
                            "description": f"Potential Double Top @ ${top_avg:.2f}. Watch neckline ${neckline:.2f}",
                            "draw_points": [{"index": idx1, "price": h1}, {"index": neckline_idx, "price": neckline}, {"index": idx2, "price": h2}]
                        })
                        break
                if patterns and patterns[-1]["pattern"].startswith("Double Top"):
                    break
        
        # === DOUBLE BOTTOM ===
        if len(swing_lows) >= 2:
            for a in range(len(swing_lows) - 1):
                for b in range(a + 1, len(swing_lows)):
                    l1_data = swing_lows[a]
                    l2_data = swing_lows[b]
                    l1, l2 = l1_data["price"], l2_data["price"]
                    idx1, idx2 = l1_data["index"], l2_data["index"]
                    
                    # KRITERIUM 1: Mindestabstand (min 10 Bars)
                    if idx2 - idx1 < min_pattern_gap:
                        continue
                    
                    # KRITERIUM 2: Ähnliche Tiefe (innerhalb 1.5× ATR)
                    if abs(l1 - l2) > atr * 1.5:
                        continue
                    
                    # KRITERIUM 3: Neckline muss signifikant höher sein (min 2× ATR)
                    neckline = max(highs[idx1:idx2+1])
                    bottom_avg = (l1 + l2) / 2
                    depth = neckline - bottom_avg
                    
                    if depth < atr * min_depth_atr:
                        continue
                    
                    # KRITERIUM 4: VORHERIGER DOWNTREND
                    # Der Kurs VOR dem ersten Bottom muss höher gewesen sein
                    # Mindestens 5 Bars vor Bottom 1 anschauen
                    pre_start = max(0, idx1 - 15)
                    pre_end = max(0, idx1 - 2)
                    if pre_end > pre_start:
                        pre_highs = highs[pre_start:pre_end]
                        pre_high_avg = sum(pre_highs) / len(pre_highs)
                        # Der Durchschnitt vor Bottom 1 muss mindestens 2× ATR höher sein als die Bottoms
                        # Sonst war es eine Seitwärtsrange, kein Downtrend
                        if pre_high_avg - bottom_avg < atr * 2:
                            continue
                    
                    # KRITERIUM 5: KLARER RALLY-PEAK zwischen den Bottoms
                    # Die Neckline muss deutlich über den Bottoms UND über dem
                    # allgemeinen Preisniveau vor/nach den Bottoms liegen
                    # Prüfe ob der Bereich zwischen den Bottoms ein klares "V" oder "W" zeigt
                    mid_section = closes[idx1:idx2+1]
                    if mid_section:
                        mid_avg = sum(mid_section) / len(mid_section)
                        # Wenn der Durchschnitt zwischen den Bottoms nahe den Bottoms liegt,
                        # ist es eine flache Range, kein W-Pattern
                        if mid_avg - bottom_avg < depth * 0.3:
                            continue
                    
                    # KRITERIUM 6: RECENCY — zweites Bottom muss in der zweiten Hälfte der Daten sein
                    if idx2 < len(data) * 0.3:
                        continue
                    
                    # KRITERIUM 7: Volume-Bestätigung
                    recent_vol = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else avg_volume
                    vol_confirmation = recent_vol > avg_volume * 0.8
                    
                    # Preis über Neckline = bestätigt
                    if current_price > neckline:
                        neckline_idx = idx1 + highs[idx1:idx2+1].index(max(highs[idx1:idx2+1]))
                        patterns.append({
                            "pattern": "Double Bottom",
                            "emoji": "",
                            "type": "bullish",
                            "level1": round(l1, 2),
                            "level2": round(l2, 2),
                            "neckline": round(neckline, 2),
                            "target": round(neckline + depth, 2),
                            "confidence": "High" if vol_confirmation else "Medium",
                            "description": f"Double Bottom @ ${bottom_avg:.2f} - Neckline ${neckline:.2f} broken. Target: ${neckline + depth:.2f}",
                            "draw_points": [{"index": idx1, "price": l1}, {"index": neckline_idx, "price": neckline}, {"index": idx2, "price": l2}]
                        })
                        break
                    elif current_price > bottom_avg + atr and current_price < neckline:
                        neckline_idx = idx1 + highs[idx1:idx2+1].index(max(highs[idx1:idx2+1]))
                        patterns.append({
                            "pattern": "Double Bottom (forming)",
                            "emoji": "",
                            "type": "bullish",
                            "level1": round(l1, 2),
                            "level2": round(l2, 2),
                            "neckline": round(neckline, 2),
                            "target": round(neckline + depth, 2),
                            "confidence": "Low",
                            "description": f"Potential Double Bottom @ ${bottom_avg:.2f}. Watch neckline ${neckline:.2f}",
                            "draw_points": [{"index": idx1, "price": l1}, {"index": neckline_idx, "price": neckline}, {"index": idx2, "price": l2}]
                        })
                        break
                if patterns and any(p["pattern"].startswith("Double Bottom") for p in patterns):
                    break
        
        # === HEAD & SHOULDERS ===
        if len(swing_highs) >= 3:
            # Prüfe die letzten 3-5 Swing Highs für H&S
            for i in range(max(0, len(swing_highs) - 5), len(swing_highs) - 2):
                for j in range(i + 1, len(swing_highs) - 1):
                    for k in range(j + 1, len(swing_highs)):
                        sh1, sh2, sh3 = swing_highs[i], swing_highs[j], swing_highs[k]
                        h1, h2, h3 = sh1["price"], sh2["price"], sh3["price"]
                        idx1, idx2, idx3 = sh1["index"], sh2["index"], sh3["index"]
                        
                        # Mindestabstand zwischen Schultern (proportional zum Lookback)
                        hs_min_gap = max(8, min_pattern_gap // 2)
                        if idx2 - idx1 < hs_min_gap or idx3 - idx2 < hs_min_gap:
                            continue

                        # Head muss höher als beide Shoulders
                        if not (h2 > h1 and h2 > h3):
                            continue

                        # Head muss mindestens 1.5× ATR höher sein (strenger)
                        if h2 - max(h1, h3) < atr * 1.5:
                            continue
                        
                        # Shoulders ähnlich hoch (innerhalb 2× ATR)
                        if abs(h1 - h3) > atr * 2:
                            continue
                        
                        # Neckline
                        neckline = min(lows[idx1:idx3+1])
                        
                        if current_price < neckline:
                            patterns.append({
                                "pattern": "Head & Shoulders",
                                "emoji": "",
                                "type": "bearish",
                                "left_shoulder": round(h1, 2),
                                "head": round(h2, 2),
                                "right_shoulder": round(h3, 2),
                                "neckline": round(neckline, 2),
                                "target": round(neckline - (h2 - neckline), 2),
                                "confidence": "High",
                                "description": f"H&S Complete! Neckline ${neckline:.2f} broken. Target: ${neckline - (h2 - neckline):.2f}",
                                "draw_points": [{"index": idx1, "price": h1}, {"index": idx2, "price": h2}, {"index": idx3, "price": h3}, {"index": idx1 + lows[idx1:idx3+1].index(min(lows[idx1:idx3+1])), "price": neckline}]
                            })
                        elif current_price < h3 * 0.97:
                            patterns.append({
                                "pattern": "Head & Shoulders (forming)",
                                "emoji": "",
                                "type": "bearish",
                                "left_shoulder": round(h1, 2),
                                "head": round(h2, 2),
                                "right_shoulder": round(h3, 2),
                                "neckline": round(neckline, 2),
                                "target": round(neckline - (h2 - neckline), 2),
                                "confidence": "Medium",
                                "description": f"H&S forming. Watch neckline @ ${neckline:.2f}",
                                "draw_points": [{"index": idx1, "price": h1}, {"index": idx2, "price": h2}, {"index": idx3, "price": h3}, {"index": idx1 + lows[idx1:idx3+1].index(min(lows[idx1:idx3+1])), "price": neckline}]
                            })
                        
                        if any(p["pattern"].startswith("Head") for p in patterns):
                            break
                    if any(p["pattern"].startswith("Head") for p in patterns):
                        break
                if any(p["pattern"].startswith("Head") for p in patterns):
                    break
        
        # === INVERSE HEAD & SHOULDERS ===
        if len(swing_lows) >= 3:
            for i in range(max(0, len(swing_lows) - 5), len(swing_lows) - 2):
                for j in range(i + 1, len(swing_lows) - 1):
                    for k in range(j + 1, len(swing_lows)):
                        sl1, sl2, sl3 = swing_lows[i], swing_lows[j], swing_lows[k]
                        l1, l2, l3 = sl1["price"], sl2["price"], sl3["price"]
                        idx1, idx2, idx3 = sl1["index"], sl2["index"], sl3["index"]
                        
                        hs_min_gap = max(8, min_pattern_gap // 2)
                        if idx2 - idx1 < hs_min_gap or idx3 - idx2 < hs_min_gap:
                            continue
                        if not (l2 < l1 and l2 < l3):
                            continue
                        if min(l1, l3) - l2 < atr * 1.5:
                            continue
                        if abs(l1 - l3) > atr * 2:
                            continue
                        
                        neckline = max(highs[idx1:idx3+1])
                        
                        if current_price > neckline:
                            patterns.append({
                                "pattern": "Inverse Head & Shoulders",
                                "emoji": "",
                                "type": "bullish",
                                "left_shoulder": round(l1, 2),
                                "head": round(l2, 2),
                                "right_shoulder": round(l3, 2),
                                "neckline": round(neckline, 2),
                                "target": round(neckline + (neckline - l2), 2),
                                "confidence": "High",
                                "description": f"Inv. H&S Complete! Neckline ${neckline:.2f} broken. Target: ${neckline + (neckline - l2):.2f}",
                                "draw_points": [{"index": idx1, "price": l1}, {"index": idx2, "price": l2}, {"index": idx3, "price": l3}, {"index": idx1 + highs[idx1:idx3+1].index(max(highs[idx1:idx3+1])), "price": neckline}]
                            })
                        elif current_price > l3 + atr:
                            patterns.append({
                                "pattern": "Inverse H&S (forming)",
                                "emoji": "",
                                "type": "bullish",
                                "left_shoulder": round(l1, 2),
                                "head": round(l2, 2),
                                "right_shoulder": round(l3, 2),
                                "neckline": round(neckline, 2),
                                "target": round(neckline + (neckline - l2), 2),
                                "confidence": "Medium",
                                "description": f"Inv. H&S forming. Watch neckline @ ${neckline:.2f}",
                                "draw_points": [{"index": idx1, "price": l1}, {"index": idx2, "price": l2}, {"index": idx3, "price": l3}, {"index": idx1 + highs[idx1:idx3+1].index(max(highs[idx1:idx3+1])), "price": neckline}]
                            })
                        
                        if any(p["pattern"].startswith("Inverse") for p in patterns):
                            break
                    if any(p["pattern"].startswith("Inverse") for p in patterns):
                        break
                if any(p["pattern"].startswith("Inverse") for p in patterns):
                    break
        
        # === TRIANGLES ===
        if len(swing_highs) >= 3 and len(swing_lows) >= 3:
            recent_highs = [h["price"] for h in swing_highs[-4:]]
            recent_lows = [l["price"] for l in swing_lows[-4:]]
            
            if len(recent_highs) >= 3 and len(recent_lows) >= 3:
                high_trend = (recent_highs[-1] - recent_highs[0]) / recent_highs[0] if recent_highs[0] > 0 else 0
                low_trend = (recent_lows[-1] - recent_lows[0]) / recent_lows[0] if recent_lows[0] > 0 else 0
                max_high = max(recent_highs)
                max_low = max(recent_lows)
                high_range = (max_high - min(recent_highs)) / max_high if max_high > 0 else 0
                low_range = (max_low - min(recent_lows)) / max_low if max_low > 0 else 0
                
                # ASCENDING TRIANGLE: Flat resistance + rising support
                if high_range < 0.02 and low_trend > 0.02:
                    resistance = sum(recent_highs) / len(recent_highs)
                    recent_high_indices = [swing_highs[-4+i]["index"] for i in range(len(recent_highs))]
                    recent_low_indices = [swing_lows[-4+i]["index"] for i in range(len(recent_lows))]
                    patterns.append({
                        "pattern": "Ascending Triangle",
                        "emoji": "⬆",
                        "type": "bullish",
                        "resistance": round(resistance, 2),
                        "target": round(resistance * 1.05, 2),
                        "confidence": "Medium",
                        "description": f"Ascending Triangle - Resistance @ ${resistance:.2f}. Breakout target +5%",
                        "draw_points": [{"index": recent_high_indices[0], "price": recent_highs[0]}, {"index": recent_high_indices[-1], "price": recent_highs[-1]}, {"index": recent_low_indices[0], "price": recent_lows[0]}, {"index": recent_low_indices[-1], "price": recent_lows[-1]}]
                    })
                
                # DESCENDING TRIANGLE: Falling resistance + flat support
                elif low_range < 0.02 and high_trend < -0.02:
                    support = sum(recent_lows) / len(recent_lows)
                    recent_high_indices = [swing_highs[-4+i]["index"] for i in range(len(recent_highs))]
                    recent_low_indices = [swing_lows[-4+i]["index"] for i in range(len(recent_lows))]
                    patterns.append({
                        "pattern": "Descending Triangle",
                        "emoji": "⬇",
                        "type": "bearish",
                        "support": round(support, 2),
                        "target": round(support * 0.95, 2),
                        "confidence": "Medium",
                        "description": f"Descending Triangle - Support @ ${support:.2f}. Breakdown target -5%",
                        "draw_points": [{"index": recent_high_indices[0], "price": recent_highs[0]}, {"index": recent_high_indices[-1], "price": recent_highs[-1]}, {"index": recent_low_indices[0], "price": recent_lows[0]}, {"index": recent_low_indices[-1], "price": recent_lows[-1]}]
                    })
                
                # SYMMETRICAL TRIANGLE: Converging trendlines
                elif high_trend < -0.01 and low_trend > 0.01:
                    apex_price = (recent_highs[-1] + recent_lows[-1]) / 2
                    range_pct = (recent_highs[-1] - recent_lows[-1]) / apex_price * 100

                    if range_pct < 5:
                        recent_high_indices = [swing_highs[-4+i]["index"] for i in range(len(recent_highs))]
                        recent_low_indices = [swing_lows[-4+i]["index"] for i in range(len(recent_lows))]
                        patterns.append({
                            "pattern": "Symmetrical Triangle",
                            "emoji": "",
                            "type": "neutral",
                            "apex": round(apex_price, 2),
                            "range": f"{range_pct:.1f}%",
                            "confidence": "Medium",
                            "description": f"Symmetrical Triangle - Apex @ ${apex_price:.2f}. Breakout imminent!",
                            "draw_points": [{"index": recent_high_indices[0], "price": recent_highs[0]}, {"index": recent_high_indices[-1], "price": recent_highs[-1]}, {"index": recent_low_indices[0], "price": recent_lows[0]}, {"index": recent_low_indices[-1], "price": recent_lows[-1]}]
                        })
        
        # === FLAGS & PENNANTS ===
        # Verbessert: Finde den tatsächlichen Pole durch Preisbewegung
        if len(closes) >= 20:
            # Suche nach dem stärksten Move in den Daten
            best_pole_end = 0
            best_pole_move = 0
            
            for pole_end in range(5, min(int(len(closes) * 0.5), 20)):
                move = (closes[pole_end] - closes[0]) / closes[0]
                if abs(move) > abs(best_pole_move):
                    best_pole_move = move
                    best_pole_end = pole_end
            
            if best_pole_end > 0 and abs(best_pole_move) > 0.08:
                pole_end = best_pole_end
                pole_move = best_pole_move
                
                flag_data = closes[pole_end:]
                flag_highs = highs[pole_end:]
                flag_lows = lows[pole_end:]
                
                if len(flag_data) >= 5:
                    flag_range = max(flag_data) - min(flag_data)
                    flag_range_pct = flag_range / closes[pole_end] if closes[pole_end] > 0 else 0
                    
                    flag_high_trend = (flag_highs[-1] - flag_highs[0]) / flag_highs[0] if flag_highs[0] > 0 else 0
                    flag_low_trend = (flag_lows[-1] - flag_lows[0]) / flag_lows[0] if flag_lows[0] > 0 else 0
                    
                    # BULL FLAG: Starker Anstieg + leichter Rückgang im Kanal
                    if pole_move > 0.08 and flag_range_pct < 0.06:
                        if flag_high_trend < 0 and flag_low_trend < 0:
                            patterns.append({
                                "pattern": "Bull Flag",
                                "emoji": "⬆",
                                "type": "bullish",
                                "pole_move": f"{pole_move*100:.1f}%",
                                "target": round(closes[-1] * (1 + pole_move), 2),
                                "confidence": "Medium",
                                "description": f"Bull Flag after {pole_move*100:.0f}% rally. Target: ${closes[-1] * (1 + pole_move):.2f}",
                                "draw_points": [{"index": 0, "price": closes[0]}, {"index": pole_end, "price": closes[pole_end]}, {"index": pole_end, "price": flag_highs[0]}, {"index": len(closes)-1, "price": flag_highs[-1]}, {"index": pole_end, "price": flag_lows[0]}, {"index": len(closes)-1, "price": flag_lows[-1]}]
                            })
                        elif flag_high_trend < 0 and flag_low_trend > 0:
                            patterns.append({
                                "pattern": "Bullish Pennant",
                                "emoji": "⬆",
                                "type": "bullish",
                                "pole_move": f"{pole_move*100:.1f}%",
                                "target": round(closes[-1] * (1 + pole_move * 0.8), 2),
                                "confidence": "Medium",
                                "description": f"Bullish Pennant after {pole_move*100:.0f}% rally. Target: ${closes[-1] * (1 + pole_move * 0.8):.2f}",
                                "draw_points": [{"index": 0, "price": closes[0]}, {"index": pole_end, "price": closes[pole_end]}, {"index": pole_end, "price": flag_highs[0]}, {"index": len(closes)-1, "price": flag_highs[-1]}, {"index": pole_end, "price": flag_lows[0]}, {"index": len(closes)-1, "price": flag_lows[-1]}]
                            })
                    
                    # BEAR FLAG: Starker Abfall + leichte Erholung
                    elif pole_move < -0.08 and flag_range_pct < 0.06:
                        if flag_high_trend > 0 and flag_low_trend > 0:
                            patterns.append({
                                "pattern": "Bear Flag",
                                "emoji": "⬇",
                                "type": "bearish",
                                "pole_move": f"{pole_move*100:.1f}%",
                                "target": round(closes[-1] * (1 + pole_move), 2),
                                "confidence": "Medium",
                                "description": f"Bear Flag after {abs(pole_move)*100:.0f}% drop. Target: ${closes[-1] * (1 + pole_move):.2f}",
                                "draw_points": [{"index": 0, "price": closes[0]}, {"index": pole_end, "price": closes[pole_end]}, {"index": pole_end, "price": flag_highs[0]}, {"index": len(closes)-1, "price": flag_highs[-1]}, {"index": pole_end, "price": flag_lows[0]}, {"index": len(closes)-1, "price": flag_lows[-1]}]
                            })
                        elif flag_high_trend < 0 and flag_low_trend > 0:
                            patterns.append({
                                "pattern": "Bearish Pennant",
                                "emoji": "⬇",
                                "type": "bearish",
                                "pole_move": f"{pole_move*100:.1f}%",
                                "target": round(closes[-1] * (1 + pole_move * 0.8), 2),
                                "confidence": "Medium",
                                "description": f"Bearish Pennant after {abs(pole_move)*100:.0f}% drop. Target: ${closes[-1] * (1 + pole_move * 0.8):.2f}",
                                "draw_points": [{"index": 0, "price": closes[0]}, {"index": pole_end, "price": closes[pole_end]}, {"index": pole_end, "price": flag_highs[0]}, {"index": len(closes)-1, "price": flag_highs[-1]}, {"index": pole_end, "price": flag_lows[0]}, {"index": len(closes)-1, "price": flag_lows[-1]}]
                            })
        
        # === CUP & HANDLE ===
        if len(closes) >= 30:
            cup_end = int(len(closes) * 0.7)
            cup_data = closes[:cup_end]
            
            if len(cup_data) >= 15:
                cup_left = cup_data[:len(cup_data)//3]
                cup_bottom = cup_data[len(cup_data)//3:2*len(cup_data)//3]
                cup_right = cup_data[2*len(cup_data)//3:]
                
                left_avg = sum(cup_left) / len(cup_left)
                bottom_avg = sum(cup_bottom) / len(cup_bottom)
                right_avg = sum(cup_right) / len(cup_right)
                
                if left_avg > bottom_avg and right_avg > bottom_avg:
                    cup_depth = (left_avg - bottom_avg) / left_avg
                    
                    if 0.10 < cup_depth < 0.40:
                        handle_data = closes[cup_end:]
                        handle_range = (max(handle_data) - min(handle_data)) / max(handle_data) if max(handle_data) > 0 else 1
                        
                        if handle_range < cup_depth * 0.5:
                            cup_lip = max(left_avg, right_avg)
                            
                            if current_price > cup_lip * 0.95:
                                cup_left_idx = int(len(cup_data) // 6)
                                cup_bottom_idx = int(len(cup_data) // 2)
                                cup_right_idx = int(2 * len(cup_data) // 3)
                                patterns.append({
                                    "pattern": "Cup & Handle",
                                    "emoji": "⬆",
                                    "type": "bullish",
                                    "cup_depth": f"{cup_depth*100:.1f}%",
                                    "breakout_level": round(cup_lip, 2),
                                    "target": round(cup_lip * (1 + cup_depth), 2),
                                    "confidence": "High" if current_price > cup_lip else "Medium",
                                    "description": f"Cup & Handle - Breakout @ ${cup_lip:.2f}. Target: ${cup_lip * (1 + cup_depth):.2f}",
                                    "draw_points": [{"index": 0, "price": left_avg}, {"index": cup_left_idx, "price": left_avg}, {"index": cup_bottom_idx, "price": bottom_avg}, {"index": cup_right_idx, "price": right_avg}, {"index": cup_end, "price": cup_lip}]
                                })
        
        # === WEDGES ===
        if len(swing_highs) >= 3 and len(swing_lows) >= 3:
            recent_highs = [h["price"] for h in swing_highs[-4:]]
            recent_lows = [l["price"] for l in swing_lows[-4:]]
            
            if len(recent_highs) >= 3 and len(recent_lows) >= 3:
                high_slope = (recent_highs[-1] - recent_highs[0]) / len(recent_highs)
                low_slope = (recent_lows[-1] - recent_lows[0]) / len(recent_lows)
                
                _slope_sum = abs(high_slope) + abs(low_slope)
                # Converging logic: normalized slope difference should be small
                # Using ratio instead of sum-based threshold for better robustness
                are_converging = (
                    abs(high_slope - low_slope) / (_slope_sum + 0.0001) < 0.4 if _slope_sum > 0 else False
                )
                
                # RISING WEDGE (bearish)
                if high_slope > 0 and low_slope > 0 and are_converging and low_slope > high_slope:
                    recent_high_indices = [swing_highs[-4+i]["index"] for i in range(len(recent_highs))]
                    recent_low_indices = [swing_lows[-4+i]["index"] for i in range(len(recent_lows))]
                    patterns.append({
                        "pattern": "Rising Wedge",
                        "emoji": "⬇",
                        "type": "bearish",
                        "target": round(recent_lows[0], 2),
                        "confidence": "Medium",
                        "description": f"Rising Wedge (bearish) - Target support @ ${recent_lows[0]:.2f}",
                        "draw_points": [{"index": recent_high_indices[0], "price": recent_highs[0]}, {"index": recent_high_indices[-1], "price": recent_highs[-1]}, {"index": recent_low_indices[0], "price": recent_lows[0]}, {"index": recent_low_indices[-1], "price": recent_lows[-1]}]
                    })
                
                # FALLING WEDGE (bullish)
                elif high_slope < 0 and low_slope < 0 and are_converging and high_slope > low_slope:
                    recent_high_indices = [swing_highs[-4+i]["index"] for i in range(len(recent_highs))]
                    recent_low_indices = [swing_lows[-4+i]["index"] for i in range(len(recent_lows))]
                    patterns.append({
                        "pattern": "Falling Wedge",
                        "emoji": "⬆",
                        "type": "bullish",
                        "target": round(recent_highs[0], 2),
                        "confidence": "Medium",
                        "description": f"Falling Wedge (bullish) - Target resistance @ ${recent_highs[0]:.2f}",
                        "draw_points": [{"index": recent_high_indices[0], "price": recent_highs[0]}, {"index": recent_high_indices[-1], "price": recent_highs[-1]}, {"index": recent_low_indices[0], "price": recent_lows[0]}, {"index": recent_low_indices[-1], "price": recent_lows[-1]}]
                    })
        
        # === BASE BREAKOUT ===
        # Lange Seitwärtsphase + Ausbruch darüber
        # Typisch für ATEX-artige Setups: Monate flach, dann Gap/Breakout
        if len(closes) >= 30 and not any(p["pattern"] in ["Double Bottom", "Cup & Handle"] for p in patterns):
            # Finde die Base: Teile Daten in erste 60% (potenzielle Base) und letzte 40%
            base_end = int(len(closes) * 0.6)
            base_data = closes[:base_end]
            breakout_data = closes[base_end:]
            
            if len(base_data) >= 15 and len(breakout_data) >= 5:
                base_high = max(highs[:base_end])
                base_low = min(lows[:base_end])
                base_avg = sum(base_data) / len(base_data)
                base_range_pct = (base_high - base_low) / base_avg if base_avg > 0 else 1
                
                # Base muss eng sein (Range < 25% des Durchschnittspreises)
                # UND der aktuelle Preis muss deutlich über der Base liegen
                breakout_pct = (current_price - base_high) / base_high if base_high > 0 else 0
                
                if base_range_pct < 0.25 and breakout_pct > 0.05:
                    # Zusätzlich: Die Mehrzahl der Base-Bars sollte in einer engen Range sein
                    tight_count = sum(1 for c in base_data if abs(c - base_avg) / base_avg < 0.08)
                    tight_pct = tight_count / len(base_data)
                    
                    if tight_pct > 0.6:
                        patterns.append({
                            "pattern": "Base Breakout",
                            "emoji": "⬆",
                            "type": "bullish",
                            "base_low": round(base_low, 2),
                            "base_high": round(base_high, 2),
                            "breakout_pct": f"+{breakout_pct*100:.1f}%",
                            "target": round(base_high + (base_high - base_low), 2),
                            "confidence": "High" if breakout_pct > 0.10 else "Medium",
                            "description": f"Base Breakout! Range ${base_low:.2f}-${base_high:.2f}, broke out +{breakout_pct*100:.1f}%. Target: ${base_high + (base_high - base_low):.2f}",
                            "draw_points": [{"index": 0, "price": base_low}, {"index": base_end, "price": base_high}, {"index": base_end, "price": base_low}, {"index": len(closes)-1, "price": current_price}]
                        })
        
        # === WYCKOFF ACCUMULATION / DISTRIBUTION ===
        # Delegiert an find_wyckoff_for_chart() mit korrekter SC/BC-Methodik
        # (alte Version benutzte falsche 25/50/25 Datenaufteilung)
        
        if len(closes) >= 60 and not any("Wyckoff" in p.get("pattern", "") for p in patterns):
            try:
                from modules.analysis import find_wyckoff_for_chart
                wyckoff_results = find_wyckoff_for_chart(data)
                for wr in wyckoff_results:
                    w_type = wr.get("type", "Accumulation")
                    w_phase = wr.get("phase", "Phase B")
                    w_score = wr.get("score", 0)
                    w_events = wr.get("events", [])
                    w_rh = wr.get("range_high", 0)
                    w_rl = wr.get("range_low", 0)
                    w_rw = w_rh - w_rl if w_rh > w_rl else 0
                    
                    if w_score >= 35:
                        event_strs = [e["label"] if isinstance(e, dict) else str(e) for e in w_events[:3]]
                        
                        if w_type == "Accumulation":
                            phase_emoji = "[+]" if "D" in w_phase else "[~]" if "C" in w_phase else "[o]"
                            target = round(w_rh + w_rw, 2)
                            p_type = "bullish"
                        else:
                            phase_emoji = "[-]" if "D" in w_phase else "[!]" if "C" in w_phase else "[o]"
                            target = round(w_rl - w_rw, 2)
                            p_type = "bearish"
                        
                        confidence = "High" if w_score >= 70 else "Medium" if w_score >= 50 else "Low"
                        
                        patterns.append({
                            "pattern": f"Wyckoff {w_type}",
                            "emoji": wr.get("emoji", ""),
                            "type": p_type,
                            "phase": w_phase,
                            "phase_emoji": phase_emoji,
                            "range_low": round(w_rl, 2),
                            "range_high": round(w_rh, 2),
                            "events": event_strs,
                            "score": w_score,
                            "target": target,
                            "confidence": confidence,
                            "description": f"Wyckoff {w_type} — {w_phase}. Range ${w_rl:.2f}-${w_rh:.2f}. Events: {', '.join(event_strs)}",
                            "draw_points": [{"index": 0, "price": w_rl}, {"index": len(data)-1, "price": w_rh}, {"index": len(data)-1, "price": (w_rl + w_rh) / 2}]
                        })
            
            except Exception:
                pass  # Wyckoff detection failed silently
        
        # === WOLFE WAVES ===
        # 5-Punkt-Reversal: Überschuss an Linie 1→3, Target = Linie 1→4
        
        if len(data) >= 30:
            try:
                wolfe_results = detect_wolfe_waves(data, lookback=len(data), min_wave_bars=3, max_wave_bars=25)
                for ww in wolfe_results:
                    pts = ww["points"]
                    conf = ww.get("confirmation", 0)
                    if ww["direction"] == "bullish":
                        emoji = "W[+]"
                        desc = (f"Wolfe Wave Long — Entry Zone ${ww['entry']:.2f}, "
                                f"Bestätigung über ${conf:.2f}, "
                                f"Stop ${ww['stop']:.2f}, Target ${ww['target']:.2f} "
                                f"(R:R {ww['rr_ratio']:.1f}x). "
                                f"Überschuss {ww['overshoot_pct']:.0f}% unter Linie 1→3. "
                                f"Punkte: ${pts['p1']['price']:.2f}→${pts['p2']['price']:.2f}→"
                                f"${pts['p3']['price']:.2f}→${pts['p4']['price']:.2f}→${pts['p5']['price']:.2f}")
                    else:
                        emoji = "W[-]"
                        desc = (f"Wolfe Wave Short — Entry Zone ${ww['entry']:.2f}, "
                                f"Bestätigung unter ${conf:.2f}, "
                                f"Stop ${ww['stop']:.2f}, Target ${ww['target']:.2f} "
                                f"(R:R {ww['rr_ratio']:.1f}x). "
                                f"Überschuss {ww['overshoot_pct']:.0f}% über Linie 1→3. "
                                f"Punkte: ${pts['p1']['price']:.2f}→${pts['p2']['price']:.2f}→"
                                f"${pts['p3']['price']:.2f}→${pts['p4']['price']:.2f}→${pts['p5']['price']:.2f}")
                    
                    patterns.append({
                        "pattern": ww["pattern"],
                        "emoji": emoji,
                        "type": ww["direction"],
                        "entry": ww["entry"],
                        "confirmation": ww.get("confirmation"),
                        "stop": ww["stop"],
                        "target": ww["target"],
                        "rr_ratio": ww["rr_ratio"],
                        "overshoot_pct": ww["overshoot_pct"],
                        "score": ww["score"],
                        "confidence": ww["confidence"],
                        "bars_ago": ww["bars_ago"],
                        "points": ww["points"],
                        "description": desc,
                        "draw_points": [{"index": pts['p1']['index'], "price": pts['p1']['price']}, {"index": pts['p2']['index'], "price": pts['p2']['price']}, {"index": pts['p3']['index'], "price": pts['p3']['price']}, {"index": pts['p4']['index'], "price": pts['p4']['price']}, {"index": pts['p5']['index'], "price": pts['p5']['price']}]
                    })
            except Exception:
                pass
        
        # =================================================================
        # CANDLESTICK PATTERNS — Letzte 1-3 Kerzen
        # =================================================================
        # Nur die letzten Kerzen analysieren (aktuell relevant)
        # Kontext wichtig: Hammer nur nach Downtrend bullish etc.
        # =================================================================
        
        if len(data) >= 5:
            try:
                # Letzte Kerzen
                c0 = data[-1]  # Aktuellste Kerze
                c1 = data[-2]  # Vorherige
                c2 = data[-3]  # Zwei zurück
                
                o0, h0, l0, cl0 = c0["high"], c0["high"], c0["low"], c0["close"]
                o0 = data[-1].get("open", data[-1]["close"])  # Manche Daten haben kein open
                # Robuster: open aus close der vorherigen Kerze ableiten wenn nötig
                o0 = c0.get("open", c1["close"])
                h0, l0, cl0 = c0["high"], c0["low"], c0["close"]
                
                o1 = c1.get("open", c2["close"])
                h1, l1, cl1 = c1["high"], c1["low"], c1["close"]
                
                o2 = c2.get("open", data[-4]["close"] if len(data) >= 4 else c2["close"])
                h2, l2, cl2 = c2["high"], c2["low"], c2["close"]
                
                # Body und Shadow Berechnungen
                body0 = abs(cl0 - o0)
                body1 = abs(cl1 - o1)
                body2 = abs(cl2 - o2)
                
                range0 = h0 - l0 if h0 > l0 else 0.001
                range1 = h1 - l1 if h1 > l1 else 0.001
                range2 = h2 - l2 if h2 > l2 else 0.001
                
                upper_shadow0 = h0 - max(cl0, o0)
                lower_shadow0 = min(cl0, o0) - l0
                upper_shadow1 = h1 - max(cl1, o1)
                lower_shadow1 = min(cl1, o1) - l1
                
                is_green0 = cl0 > o0
                is_green1 = cl1 > o1
                is_green2 = cl2 > o2
                
                # Kontext: Mini-Trend der letzten 5-10 Kerzen
                lookback_trend = closes[-10:-1] if len(closes) >= 11 else closes[:-1]
                trend_start = lookback_trend[0] if lookback_trend else cl0
                trend_end = lookback_trend[-1] if lookback_trend else cl0
                recent_trend = (trend_end - trend_start) / trend_start if trend_start > 0 else 0
                is_downtrend = recent_trend < -0.02
                is_uptrend = recent_trend > 0.02
                
                # ─── SINGLE CANDLE PATTERNS ───
                
                # HAMMER (bullish) — Langer unterer Schatten, kleiner Body oben
                # Bedingung: nach Downtrend, unterer Schatten ≥ 2x Body, oberer Schatten klein
                if (is_downtrend and
                    lower_shadow0 >= body0 * 2 and
                    upper_shadow0 <= body0 * 0.5 and
                    body0 > range0 * 0.05):  # Nicht komplett Doji
                    patterns.append({
                        "pattern": "Hammer",
                        "emoji": "",
                        "type": "bullish",
                        "confidence": "High" if is_green0 else "Medium",
                        "description": f"Hammer @ ${cl0:.2f} nach Downtrend — Käufer wehren sich am Tief. {'Grüner Body = stärker' if is_green0 else 'Roter Body = Bestätigung abwarten'}",
                        "draw_points": [{"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}, {"index": len(data)-1, "price": cl0}]
                    })
                
                # INVERTED HAMMER (bullish) — Langer oberer Schatten, kleiner Body unten, nach Downtrend
                if (is_downtrend and
                    upper_shadow0 >= body0 * 2 and
                    lower_shadow0 <= body0 * 0.5 and
                    body0 > range0 * 0.05):
                    patterns.append({
                        "pattern": "Inverted Hammer",
                        "emoji": "⬆",
                        "type": "bullish",
                        "confidence": "Medium",
                        "description": f"Inverted Hammer @ ${cl0:.2f} — Kaufdruck kommt auf, Bestätigung durch nächste grüne Kerze nötig",
                        "draw_points": [{"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}, {"index": len(data)-1, "price": cl0}]
                    })
                
                # SHOOTING STAR (bearish) — Wie Inverted Hammer aber nach Uptrend
                if (is_uptrend and
                    upper_shadow0 >= body0 * 2 and
                    lower_shadow0 <= body0 * 0.5 and
                    body0 > range0 * 0.05):
                    patterns.append({
                        "pattern": "Shooting Star",
                        "emoji": "⭐⬇",
                        "type": "bearish",
                        "confidence": "High" if not is_green0 else "Medium",
                        "description": f"Shooting Star @ ${cl0:.2f} nach Uptrend — Verkäufer drücken vom Hoch. {'Roter Body = stärker' if not is_green0 else 'Grüner Body = schwächer'}",
                        "draw_points": [{"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}, {"index": len(data)-1, "price": cl0}]
                    })
                
                # HANGING MAN (bearish) — Wie Hammer aber nach Uptrend
                if (is_uptrend and
                    lower_shadow0 >= body0 * 2 and
                    upper_shadow0 <= body0 * 0.5 and
                    body0 > range0 * 0.05):
                    patterns.append({
                        "pattern": "Hanging Man",
                        "emoji": "",
                        "type": "bearish",
                        "confidence": "Medium",
                        "description": f"Hanging Man @ ${cl0:.2f} nach Uptrend — Verkaufsdruck nimmt zu trotz Erholung",
                        "draw_points": [{"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}, {"index": len(data)-1, "price": cl0}]
                    })
                
                # DOJI — Sehr kleiner Body, zeigt Unentschlossenheit
                if body0 <= range0 * 0.10 and range0 > atr * 0.3:
                    # Dragonfly Doji (langer unterer Schatten)
                    if lower_shadow0 > range0 * 0.6:
                        doji_type = "Dragonfly Doji"
                        doji_emoji = "D"
                        doji_bias = "bullish" if is_downtrend else "neutral"
                        doji_desc = "Dragonfly Doji — Starke Ablehnung vom Tief"
                    # Gravestone Doji (langer oberer Schatten)
                    elif upper_shadow0 > range0 * 0.6:
                        doji_type = "Gravestone Doji"
                        doji_emoji = "G"
                        doji_bias = "bearish" if is_uptrend else "neutral"
                        doji_desc = "Gravestone Doji — Starke Ablehnung vom Hoch"
                    else:
                        doji_type = "Doji"
                        doji_emoji = "+"
                        doji_bias = "neutral"
                        doji_desc = "Doji — Markt unentschlossen, warte auf Richtung"
                    
                    patterns.append({
                        "pattern": doji_type,
                        "emoji": doji_emoji,
                        "type": doji_bias,
                        "confidence": "Medium" if doji_bias != "neutral" else "Low",
                        "description": f"{doji_desc} @ ${cl0:.2f}",
                        "draw_points": [{"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}, {"index": len(data)-1, "price": (o0 + cl0) / 2}]
                    })
                
                # MARUBOZU — Große Kerze fast ohne Schatten (starkes Momentum)
                if body0 > range0 * 0.85 and body0 > atr * 1.2:
                    maru_type = "Bullish Marubozu" if is_green0 else "Bearish Marubozu"
                    maru_emoji = "[**]UP" if is_green0 else "[**]DN"
                    patterns.append({
                        "pattern": maru_type,
                        "emoji": maru_emoji,
                        "type": "bullish" if is_green0 else "bearish",
                        "confidence": "High",
                        "description": f"{maru_type} @ ${cl0:.2f} — Starkes Momentum, {'Käufer' if is_green0 else 'Verkäufer'} dominieren komplett",
                        "draw_points": [{"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}, {"index": len(data)-1, "price": cl0}]
                    })
                
                # ─── TWO CANDLE PATTERNS ───
                
                # BULLISH ENGULFING — Grüne Kerze verschluckt vorherige rote komplett
                if (not is_green1 and is_green0 and
                    o0 <= cl1 and cl0 >= o1 and
                    body0 > body1 * 0.8):
                    conf = "High" if is_downtrend else "Medium"
                    patterns.append({
                        "pattern": "Bullish Engulfing",
                        "emoji": "⬆",
                        "type": "bullish",
                        "confidence": conf,
                        "description": f"Bullish Engulfing @ ${cl0:.2f} — Grüne Kerze verschluckt rote. {'Nach Downtrend = starkes Reversal-Signal' if is_downtrend else 'Stärker nach Pullback'}",
                        "draw_points": [{"index": len(data)-2, "price": h1}, {"index": len(data)-2, "price": l1}, {"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}]
                    })
                
                # BEARISH ENGULFING — Rote Kerze verschluckt vorherige grüne komplett
                if (is_green1 and not is_green0 and
                    o0 >= cl1 and cl0 <= o1 and
                    body0 > body1 * 0.8):
                    conf = "High" if is_uptrend else "Medium"
                    patterns.append({
                        "pattern": "Bearish Engulfing",
                        "emoji": "⬇",
                        "type": "bearish",
                        "confidence": conf,
                        "description": f"Bearish Engulfing @ ${cl0:.2f} — Rote Kerze verschluckt grüne. {'Nach Uptrend = starkes Reversal-Signal' if is_uptrend else 'Stärker nach Bounce'}",
                        "draw_points": [{"index": len(data)-2, "price": h1}, {"index": len(data)-2, "price": l1}, {"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}]
                    })
                
                # PIERCING LINE (bullish) — Rote Kerze, dann grüne die über 50% der roten schließt
                if (not is_green1 and is_green0 and is_downtrend and
                    o0 < cl1 and  # Gap down open
                    cl0 > o1 - body1 * 0.5 and cl0 < o1):  # Schließt über 50% der roten
                    patterns.append({
                        "pattern": "Piercing Line",
                        "emoji": "⬆",
                        "type": "bullish",
                        "confidence": "Medium",
                        "description": f"Piercing Line @ ${cl0:.2f} — Käufer drehen nach Gap Down, Recovery über 50%",
                        "draw_points": [{"index": len(data)-2, "price": h1}, {"index": len(data)-2, "price": l1}, {"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}]
                    })
                
                # DARK CLOUD COVER (bearish) — Gegenteil von Piercing
                if (is_green1 and not is_green0 and is_uptrend and
                    o0 > cl1 and  # Gap up open
                    cl0 < cl1 - body1 * 0.5 and cl0 > o1):  # Schließt unter 50% der grünen
                    patterns.append({
                        "pattern": "Dark Cloud Cover",
                        "emoji": "⬇",
                        "type": "bearish",
                        "confidence": "Medium",
                        "description": f"Dark Cloud Cover @ ${cl0:.2f} — Verkäufer drehen nach Gap Up, Rückgang über 50%",
                        "draw_points": [{"index": len(data)-2, "price": h1}, {"index": len(data)-2, "price": l1}, {"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}]
                    })
                
                # TWEEZER BOTTOM (bullish) — Zwei Kerzen mit fast gleichem Tief
                if (is_downtrend and
                    abs(l0 - l1) <= atr * 0.15 and
                    not is_green1 and is_green0):
                    patterns.append({
                        "pattern": "Tweezer Bottom",
                        "emoji": "⬆",
                        "type": "bullish",
                        "confidence": "Medium",
                        "description": f"Tweezer Bottom @ ${l0:.2f} — Doppeltes Tief auf gleichem Level, Support bestätigt",
                        "draw_points": [{"index": len(data)-2, "price": l1}, {"index": len(data)-1, "price": l0}]
                    })
                
                # TWEEZER TOP (bearish) — Zwei Kerzen mit fast gleichem Hoch
                if (is_uptrend and
                    abs(h0 - h1) <= atr * 0.15 and
                    is_green1 and not is_green0):
                    patterns.append({
                        "pattern": "Tweezer Top",
                        "emoji": "⬇",
                        "type": "bearish",
                        "confidence": "Medium",
                        "description": f"Tweezer Top @ ${h0:.2f} — Doppeltes Hoch auf gleichem Level, Resistance bestätigt",
                        "draw_points": [{"index": len(data)-2, "price": h1}, {"index": len(data)-1, "price": h0}]
                    })
                
                # ─── THREE CANDLE PATTERNS ───
                
                # MORNING STAR (bullish) — Rote Kerze, kleiner Body (Doji/Spinning), grüne Kerze
                if (not is_green2 and is_green0 and
                    body2 > atr * 0.5 and body0 > atr * 0.5 and  # Große äußere Kerzen
                    body1 < body2 * 0.4 and body1 < body0 * 0.4 and  # Kleine Mitte
                    cl0 > o2 - body2 * 0.5 and  # Grüne schließt über 50% der roten
                    is_downtrend):
                    patterns.append({
                        "pattern": "Morning Star",
                        "emoji": "⬆",
                        "type": "bullish",
                        "confidence": "High",
                        "description": f"Morning Star @ ${cl0:.2f} — Klassisches 3-Kerzen Reversal nach Downtrend. Starkes Kaufsignal",
                        "draw_points": [{"index": len(data)-3, "price": h2}, {"index": len(data)-3, "price": l2}, {"index": len(data)-2, "price": h1}, {"index": len(data)-2, "price": l1}, {"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}]
                    })
                
                # EVENING STAR (bearish) — Grüne Kerze, kleiner Body, rote Kerze
                if (is_green2 and not is_green0 and
                    body2 > atr * 0.5 and body0 > atr * 0.5 and
                    body1 < body2 * 0.4 and body1 < body0 * 0.4 and
                    cl0 < cl2 + body2 * 0.5 and
                    is_uptrend):
                    patterns.append({
                        "pattern": "Evening Star",
                        "emoji": "⬇",
                        "type": "bearish",
                        "confidence": "High",
                        "description": f"Evening Star @ ${cl0:.2f} — Klassisches 3-Kerzen Reversal nach Uptrend. Starkes Verkaufssignal",
                        "draw_points": [{"index": len(data)-3, "price": h2}, {"index": len(data)-3, "price": l2}, {"index": len(data)-2, "price": h1}, {"index": len(data)-2, "price": l1}, {"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}]
                    })
                
                # THREE WHITE SOLDIERS (bullish) — Drei aufeinanderfolgende grüne Kerzen
                if (is_green0 and is_green1 and is_green2 and
                    cl0 > cl1 > cl2 and  # Steigend
                    body0 > atr * 0.4 and body1 > atr * 0.4 and body2 > atr * 0.4 and  # Substantielle Bodies
                    upper_shadow0 < body0 * 0.3):  # Wenig oberer Schatten (Stärke)
                    patterns.append({
                        "pattern": "Three White Soldiers",
                        "emoji": "",
                        "type": "bullish",
                        "confidence": "High" if is_downtrend else "Medium",
                        "description": f"Three White Soldiers — Drei starke grüne Kerzen. {'Reversal nach Downtrend!' if is_downtrend else 'Trendfortsetzung'}",
                        "draw_points": [{"index": len(data)-3, "price": l2}, {"index": len(data)-3, "price": h2}, {"index": len(data)-2, "price": l1}, {"index": len(data)-2, "price": h1}, {"index": len(data)-1, "price": l0}, {"index": len(data)-1, "price": h0}]
                    })
                
                # THREE BLACK CROWS (bearish) — Drei aufeinanderfolgende rote Kerzen
                if (not is_green0 and not is_green1 and not is_green2 and
                    cl0 < cl1 < cl2 and
                    body0 > atr * 0.4 and body1 > atr * 0.4 and body2 > atr * 0.4 and
                    lower_shadow0 < body0 * 0.3):
                    patterns.append({
                        "pattern": "Three Black Crows",
                        "emoji": "⬛⬛⬛",
                        "type": "bearish",
                        "confidence": "High" if is_uptrend else "Medium",
                        "description": f"Three Black Crows — Drei starke rote Kerzen. {'Reversal nach Uptrend!' if is_uptrend else 'Trendfortsetzung'}",
                        "draw_points": [{"index": len(data)-3, "price": h2}, {"index": len(data)-3, "price": l2}, {"index": len(data)-2, "price": h1}, {"index": len(data)-2, "price": l1}, {"index": len(data)-1, "price": h0}, {"index": len(data)-1, "price": l0}]
                    })
            
            except Exception:
                pass  # Candlestick detection failed
        
        # =================================================================
        # VOLUME IMBALANCES — ICT Body-to-Body Gaps
        # =================================================================
        if len(data) >= 20:
            try:
                vi_result = detect_volume_imbalances(data)
                
                # Zeige die nächsten unfilled Zonen als Pattern
                for zone in vi_result["unfilled_bull"][:3]:
                    dist_pct = (current_price - zone["zone_high"]) / current_price * 100 if current_price > 0 else 0
                    type_label = {"VI": "Volume Imbalance", "FVG": "Fair Value Gap", "OG": "Opening Gap"}[zone["type"]]
                    str_stars = "⭐" * zone["strength"]
                    patterns.append({
                        "pattern": f"Bullish {type_label}",
                        "emoji": "",
                        "type": "bullish",
                        "zone_high": zone["zone_high"],
                        "zone_low": zone["zone_low"],
                        "zone_mid": zone["zone_mid"],
                        "gap_pct": zone["gap_pct"],
                        "vol_ratio": zone["vol_ratio"],
                        "ce_filled": zone["ce_filled"],
                        "confidence": "High" if zone["strength"] >= 3 else "Medium" if zone["strength"] >= 2 else "Low",
                        "description": f"Bullish {zone['type']} @ ${zone['zone_low']:.2f}-${zone['zone_high']:.2f} ({zone['gap_pct']:.1f}% gap). "
                                       f"Dist: {dist_pct:.1f}% unter Preis. Vol: {zone['vol_ratio']:.1f}x. {str_stars} "
                                       f"{'CE 50% berührt' if zone['ce_filled'] else 'Unfilled'}",
                        "draw_points": [{"index": 0, "price": zone["zone_low"]}, {"index": len(data)-1, "price": zone["zone_high"]}]
                    })
                
                for zone in vi_result["unfilled_bear"][:3]:
                    dist_pct = (zone["zone_low"] - current_price) / current_price * 100 if current_price > 0 else 0
                    type_label = {"VI": "Volume Imbalance", "FVG": "Fair Value Gap", "OG": "Opening Gap"}[zone["type"]]
                    str_stars = "⭐" * zone["strength"]
                    patterns.append({
                        "pattern": f"Bearish {type_label}",
                        "emoji": "",
                        "type": "bearish",
                        "zone_high": zone["zone_high"],
                        "zone_low": zone["zone_low"],
                        "zone_mid": zone["zone_mid"],
                        "gap_pct": zone["gap_pct"],
                        "vol_ratio": zone["vol_ratio"],
                        "ce_filled": zone["ce_filled"],
                        "confidence": "High" if zone["strength"] >= 3 else "Medium" if zone["strength"] >= 2 else "Low",
                        "description": f"Bearish {zone['type']} @ ${zone['zone_low']:.2f}-${zone['zone_high']:.2f} ({zone['gap_pct']:.1f}% gap). "
                                       f"Dist: {dist_pct:.1f}% über Preis. Vol: {zone['vol_ratio']:.1f}x. {str_stars} "
                                       f"{'CE 50% berührt' if zone['ce_filled'] else 'Unfilled'}",
                        "draw_points": [{"index": 0, "price": zone["zone_low"]}, {"index": len(data)-1, "price": zone["zone_high"]}]
                    })
                
                # Stats als Meta-Info
                stats = vi_result["stats"]
                if stats["total"] > 0:
                    patterns.append({
                        "pattern": "VI Stats",
                        "emoji": "",
                        "type": "info",
                        "confidence": "Info",
                        "description": f"Volume Imbalances: {stats['total']} total, {stats['unfilled']} unfilled, "
                                       f"Fill Rate: {stats['fill_rate']:.0f}%. "
                                       f"Bull Support: {stats['bull_unfilled']}, Bear Resistance: {stats['bear_unfilled']}",
                        "draw_points": []
                    })
            
            except Exception:
                pass
        
        # =================================================================
        # ORDER BLOCKS — ICT Institutionelle Entry-Zonen (nur Aktien)
        # =================================================================
        ob_result = None
        if len(data) >= 20:
            try:
                ob_result = detect_order_blocks(data)
                
                for ob in ob_result["bullish_obs"][:3]:
                    stars = "⭐" * ob["strength"]
                    patterns.append({
                        "pattern": "Bullish Order Block",
                        "emoji": "",
                        "type": "bullish",
                        "confidence": "High" if ob["strength"] >= 3 else "Medium" if ob["strength"] >= 2 else "Low",
                        "description": f"Bullish OB @ ${ob['ob_low']:.2f}-${ob['ob_high']:.2f}. "
                                       f"Impuls: {ob['impulse_size']:.1f}x ATR, Vol: {ob['vol_ratio']:.1f}x. "
                                       f"Dist: {ob['dist_pct']:.1f}% unter Preis. {stars} "
                                       f"— Limit Buy bei Rückkehr in diese Zone.",
                        "draw_points": [{"index": 0, "price": ob['ob_low']}, {"index": len(data)-1, "price": ob['ob_high']}]
                    })
                
                for ob in ob_result["bearish_obs"][:3]:
                    stars = "⭐" * ob["strength"]
                    patterns.append({
                        "pattern": "Bearish Order Block",
                        "emoji": "",
                        "type": "bearish",
                        "confidence": "High" if ob["strength"] >= 3 else "Medium" if ob["strength"] >= 2 else "Low",
                        "description": f"Bearish OB @ ${ob['ob_low']:.2f}-${ob['ob_high']:.2f}. "
                                       f"Impuls: {ob['impulse_size']:.1f}x ATR, Vol: {ob['vol_ratio']:.1f}x. "
                                       f"Dist: {ob['dist_pct']:.1f}% über Preis. {stars} "
                                       f"— Limit Sell bei Rückkehr in diese Zone.",
                        "draw_points": [{"index": 0, "price": ob['ob_low']}, {"index": len(data)-1, "price": ob['ob_high']}]
                    })
            except Exception:
                pass
        
        # =================================================================
        # LIQUIDITY LEVELS — Buyside / Sellside (nur Aktien)
        # =================================================================
        liq_result = None
        if len(data) >= 20:
            try:
                liq_result = detect_liquidity_levels(data)
                
                for lv in liq_result["buyside"][:3]:
                    stars = "⭐" * lv["strength"]
                    patterns.append({
                        "pattern": f"Buyside Liquidity ({lv['type']})",
                        "emoji": "⬆",
                        "type": "info",
                        "confidence": "High" if lv["touches"] >= 3 else "Medium" if lv["touches"] >= 2 else "Low",
                        "description": f"BSL @ ${lv['level']:.2f} ({lv['touches']}x touches). "
                                       f"Dist: {lv['dist_pct']:.1f}% über Preis. {stars} "
                                       f"Buy Stops der Shorts liegen hier — potentieller TP für Longs.",
                        "draw_points": [{"index": 0, "price": lv['level']}, {"index": len(data)-1, "price": lv['level']}]
                    })
                
                for lv in liq_result["sellside"][:3]:
                    stars = "⭐" * lv["strength"]
                    patterns.append({
                        "pattern": f"Sellside Liquidity ({lv['type']})",
                        "emoji": "⬇",
                        "type": "info",
                        "confidence": "High" if lv["touches"] >= 3 else "Medium" if lv["touches"] >= 2 else "Low",
                        "description": f"SSL @ ${lv['level']:.2f} ({lv['touches']}x touches). "
                                       f"Dist: {lv['dist_pct']:.1f}% unter Preis. {stars} "
                                       f"Sell Stops der Longs liegen hier — potentieller TP für Shorts.",
                        "draw_points": [{"index": 0, "price": lv['level']}, {"index": len(data)-1, "price": lv['level']}]
                    })
            except Exception:
                pass
        
        # =================================================================
        # SMC SETUP — FVG + OB + Liquidity kombiniert (nur Aktien)
        # =================================================================
        if vi_result and ob_result and liq_result:
            try:
                smc = format_smc_setup(vi_result, ob_result, liq_result, current_price, ohlcv_data=data)
                
                # Fix 11: Beide Setups anzeigen wenn vorhanden
                for setup_key in ("long_setup", "short_setup"):
                    s = smc.get(setup_key, {})
                    if s.get("has_setup"):
                        conf_text = " | ".join(s["confluence"])
                        patterns.append({
                            "pattern": f" SMC {s['direction']} Setup",
                            "emoji": "",
                            "type": "bullish" if s["direction"] == "LONG" else "bearish",
                            "confidence": "High" if s["score"] >= 60 else "Medium" if s["score"] >= 40 else "Low",
                            "description": f"{s['description']}\n"
                                           f"Confluence: {conf_text}",
                            "draw_points": []
                        })
            except Exception:
                pass
        
        # === KONFLIKT-FILTER ===
        # Widersprüchliche Patterns entfernen (z.B. Double Bottom + Head & Shoulders)
        # Regel: Wenn bullish UND bearish Patterns gleicher Kategorie, behalte das mit höherer Konfidenz
        structural_bulls = [p for p in patterns if p["type"] == "bullish" and p.get("confidence") in ("High", "Medium") and p["pattern"] in ("Double Bottom", "Cup & Handle", "Inv. Head & Shoulders", "Falling Wedge", "Base Breakout")]
        structural_bears = [p for p in patterns if p["type"] == "bearish" and p.get("confidence") in ("High", "Medium") and p["pattern"] in ("Double Top", "Head & Shoulders", "Rising Wedge")]

        if structural_bulls and structural_bears:
            # Behalte nur die Seite mit der höheren Konfidenz
            bull_has_high = any(p.get("confidence") == "High" for p in structural_bulls)
            bear_has_high = any(p.get("confidence") == "High" for p in structural_bears)
            if bull_has_high and not bear_has_high:
                patterns = [p for p in patterns if p not in structural_bears]
            elif bear_has_high and not bull_has_high:
                patterns = [p for p in patterns if p not in structural_bulls]

        # (Post-Processing für detect_index ist jetzt AUSSERHALB des try-Blocks)

    except Exception as e:
        log.warning(f"detect_chart_patterns error: {e}")
        import traceback
        traceback.print_exc()

    # ═══════════════════════════════════════════════════════════════════
    # POST-PROCESSING: detect_index für Chart-Marker zuweisen
    # WICHTIG: Eigener try-Block, damit es IMMER läuft, auch wenn
    # oben eine Exception geworfen wurde und patterns nur teilweise gefüllt ist.
    # ═══════════════════════════════════════════════════════════════════
    try:
        if patterns and ohlcv_data:
            data_len = min(50, len(ohlcv_data))  # lookback
            data = ohlcv_data[-data_len:]
            offset = len(ohlcv_data) - data_len

            structural_pats = {"Double Top", "Double Top (forming)", "Head & Shoulders", "Head & Shoulders (forming)",
                               "Double Bottom", "Double Bottom (forming)", "Inv. Head & Shoulders", "Inv. H&S (forming)"}
            candle_pats = {"Hammer", "Inverted Hammer", "Shooting Star", "Hanging Man", "Doji",
                          "Dragonfly Doji", "Gravestone Doji", "Bullish Marubozu", "Bearish Marubozu",
                          "Bullish Engulfing", "Bearish Engulfing", "Piercing Line", "Dark Cloud Cover",
                          "Tweezer Bottom", "Tweezer Top", "Morning Star", "Evening Star",
                          "Three White Soldiers", "Three Black Crows"}

            for p in patterns:
                if p.get("detect_index") is not None:
                    continue  # Bereits gesetzt, nicht überschreiben

                pname = p.get("pattern", "")

                if pname in structural_pats:
                    target_price = p.get("level2") or p.get("right_shoulder") or p.get("head")
                    if target_price:
                        best_idx = len(data) - 1
                        best_diff = float("inf")
                        for i in range(len(data) - 1, -1, -1):
                            diff = abs(data[i]["high"] - target_price)
                            diff_low = abs(data[i]["low"] - target_price)
                            d_min = min(diff, diff_low)
                            if d_min < best_diff:
                                best_diff = d_min
                                best_idx = i
                        p["detect_index"] = best_idx + offset
                    else:
                        p["detect_index"] = len(ohlcv_data) - 1

                elif pname in candle_pats:
                    p["detect_index"] = len(ohlcv_data) - 1

                elif "Triangle" in pname or "Wedge" in pname or "Flag" in pname or "Pennant" in pname:
                    p["detect_index"] = offset + int(data_len * 0.7)

                elif "Cup" in pname:
                    p["detect_index"] = len(ohlcv_data) - 1

                elif "Wyckoff" in pname or "Wolfe" in pname:
                    p["detect_index"] = offset + int(data_len * 0.8)

                elif "Base Breakout" in pname:
                    p["detect_index"] = offset + int(data_len * 0.6)

                elif "Volume Imbalance" in pname or "Fair Value Gap" in pname or "Opening Gap" in pname:
                    zone_mid = p.get("zone_mid") or p.get("zone_high", 0)
                    if zone_mid:
                        best_idx = len(data) - 1
                        best_diff = float("inf")
                        for i in range(len(data)):
                            d_min = min(abs(data[i]["high"] - zone_mid), abs(data[i]["low"] - zone_mid))
                            if d_min < best_diff:
                                best_diff = d_min
                                best_idx = i
                        p["detect_index"] = best_idx + offset
                    else:
                        p["detect_index"] = len(ohlcv_data) - 1

                elif "Order Block" in pname:
                    ob_level = p.get("zone_high") or p.get("zone_low", 0)
                    best_idx = len(data) - 1
                    best_diff = float("inf")
                    for i in range(len(data)):
                        d_min = min(abs(data[i]["high"] - ob_level), abs(data[i]["low"] - ob_level))
                        if d_min < best_diff:
                            best_diff = d_min
                            best_idx = i
                    p["detect_index"] = best_idx + offset

                else:
                    p["detect_index"] = len(ohlcv_data) - 1

    except Exception as e:
        log.warning(f"detect_index post-processing error: {e}")

    return patterns