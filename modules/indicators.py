"""
Technical Indicator Functions Module

This module contains all core technical indicator calculations used by the TradingBot scanner:
- Price Position Indicators: calculate_close_position
- Volatility Indicators: estimate_crypto_atr, calculate_atr_from_ohlc, calculate_atr_14
- Trend Indicators: calculate_adx, calculate_sma, calculate_ema, calculate_ema_series
- Momentum Indicators: calculate_rsi_from_bars, calculate_macd, calculate_stochastic, calculate_ma_distance
- Volume Indicators: calculate_vwap, calculate_obv

All functions are designed to work with OHLC(V) data and return standardized outputs for
technical analysis, backtesting, and real-time scanning.
"""

import math


def calculate_close_position(high, low, close, min_range_pct=1.0):
    """
    Berechnet Close Position mit Sicherheits-Checks.

    Close Position = (close - low) / (high - low)
    Zeigt wo der Preis innerhalb der Tagesrange geschlossen hat:
    - 1.0 = Close am High (bullisch)
    - 0.5 = Close in der Mitte
    - 0.0 = Close am Low (bärisch)

    WICHTIG: Morgens ist die Range sehr klein → Close Position unzuverlässig!
    Wir geben None zurück wenn die Range < min_range_pct ist.

    Bei min_range_pct=1.0%:
    - $50 Aktie braucht mindestens $0.50 Range
    - $100 Aktie braucht mindestens $1.00 Range
    - $50 Aktie braucht mindestens $0.25 Range
    - $100 Aktie braucht mindestens $0.50 Range
    - Balance zwischen Zuverlässigkeit und früher Erkennung

    Args:
        high: Tageshoch
        low: Tagestief
        close: Aktueller Preis
        min_range_pct: Mindest-Range in % für zuverlässige Berechnung (default 1.0%)

    Returns:
        Close Position (0-1) oder None wenn nicht berechenbar
    """
    if high is None or low is None or close is None:
        return None
    if high <= 0 or low <= 0:
        return None
    if high == low:
        return None  # Keine Range = keine Close Position

    # Prüfe ob genug Range vorhanden (Morgen-Problem vermeiden)
    range_pct = ((high - low) / low) * 100
    if range_pct < min_range_pct:
        return None  # Zu wenig Range für zuverlässige Close Position

    close_pos = (close - low) / (high - low)

    # Clamp auf 0-1 (kann >1 oder <0 sein wenn close außerhalb range)
    return max(0.0, min(1.0, close_pos))


def estimate_crypto_atr(market_cap, high_24h=None, low_24h=None, price=None):
    """V69: Zentrale ATR-Schätzung für Crypto — bevorzugt echten Range wenn verfügbar.

    Wenn high_24h/low_24h/price vorhanden → echte Tages-Range als ATR-Proxy.
    Sonst Fallback auf Market-Cap-Tiers (weniger genau).
    """
    # Echte Range nutzen wenn verfügbar (10x genauer als statische Tiers)
    if high_24h and low_24h and price and price > 0 and high_24h > low_24h:
        real_atr = (high_24h - low_24h) / price * 100
        if real_atr >= 0.1:  # Mindestens 0.1% Range (Sanity Check)
            return real_atr
    # Fallback: Market-Cap-basierte Schätzung
    # V69.1 AUDIT FIX: Mega Cap 2.5→3.5% (BTC reale ATR typisch 3-5%, 2.5% war zu niedrig)
    mc = market_cap or 0
    if mc > 100_000_000_000:   return 3.5
    elif mc > 10_000_000_000:  return 4.0
    elif mc > 1_000_000_000:   return 6.5
    elif mc > 100_000_000:     return 9.5
    else:                      return 15.0


def calculate_atr_from_ohlc(high, low, close, prev_close):
    """
    Berechnet TRUE RANGE (single bar) — NICHT ATR!

    HINWEIS: Echtes ATR = Durchschnitt über 14 Perioden.
    Mit nur einer Kerze können wir nur die True Range berechnen.
    Das Ergebnis kann an ruhigen Tagen zu niedrig und
    an volatilen Tagen zu hoch sein.

    True Range = max(
        High - Low,
        |High - Previous Close|,
        |Low - Previous Close|
    )

    Für echtes ATR: Nutze calculate_atr_14() mit Multi-Bar-Daten.
    """
    if high <= 0 or low <= 0 or close <= 0:
        return 0

    tr1 = high - low
    tr2 = abs(high - prev_close) if prev_close > 0 else 0
    tr3 = abs(low - prev_close) if prev_close > 0 else 0

    true_range = max(tr1, tr2, tr3)

    # True Range als Prozent vom Preis (für Vergleichbarkeit)
    tr_pct = (true_range / close) * 100 if close > 0 else 0

    return round(tr_pct, 2)


def calculate_atr_14(ohlcv_data):
    """
    Berechnet echtes 14-Perioden ATR aus OHLCV-Daten.

    Nutze diese Funktion wenn Multi-Bar-Daten verfügbar sind
    (z.B. im Chart Analyzer, Harmonic Scanner, Wyckoff Scanner).

    Returns: ATR als absoluter Wert und als Prozent
    """
    if not ohlcv_data or len(ohlcv_data) < 15:
        return 0, 0

    true_ranges = []
    for i in range(1, len(ohlcv_data)):
        h = ohlcv_data[i]["high"]
        l = ohlcv_data[i]["low"]
        pc = ohlcv_data[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        true_ranges.append(tr)

    # Wilder's Smoothed ATR (wie TradingView)
    atr = sum(true_ranges[:14]) / 14
    for tr in true_ranges[14:]:
        atr = (atr * 13 + tr) / 14

    current_price = ohlcv_data[-1]["close"]
    atr_pct = (atr / current_price * 100) if current_price > 0 else 0

    return round(atr, 4), round(atr_pct, 2)


def calculate_adx(bars, period=14):
    """
    Berechnet ADX (Average Directional Index) aus OHLC-Daten.
    ADX < 20 = kein Trend (Konsolidierung), ADX steigend von <20 = Breakout beginnt.

    Returns: (adx_value, adx_prev) oder (None, None) wenn nicht genug Daten
    """
    if not bars or len(bars) < period + 2:
        return None, None

    plus_dm_list = []
    minus_dm_list = []
    tr_list = []

    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_high = bars[i-1]["high"]
        prev_low = bars[i-1]["low"]
        prev_close = bars[i-1]["close"]

        # True Range
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

        # Directional Movement
        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < period:
        return None, None

    # Smoothed averages (Wilder's smoothing)
    atr = sum(tr_list[:period]) / period
    plus_dm_avg = sum(plus_dm_list[:period]) / period
    minus_dm_avg = sum(minus_dm_list[:period]) / period

    dx_list = []

    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        plus_dm_avg = (plus_dm_avg * (period - 1) + plus_dm_list[i]) / period
        minus_dm_avg = (minus_dm_avg * (period - 1) + minus_dm_list[i]) / period

        plus_di = (plus_dm_avg / atr * 100) if atr > 0 else 0
        minus_di = (minus_dm_avg / atr * 100) if atr > 0 else 0

        di_sum = plus_di + minus_di
        dx = (abs(plus_di - minus_di) / di_sum * 100) if di_sum > 0 else 0
        dx_list.append(dx)

    if len(dx_list) < period:
        return None, None

    # ADX = smoothed DX
    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period

    # ADX von 5 Bars vorher: Berechne ADX bis zum 5.-letzten DX-Wert
    adx_prev = None
    if len(dx_list) >= period + 5:
        adx_prev = sum(dx_list[:period]) / period
        # Smoothe bis zum 5.-letzten DX-Wert (= ADX Stand von vor ~5 Bars)
        end_idx = len(dx_list) - 5
        for i in range(period, end_idx):
            adx_prev = (adx_prev * (period - 1) + dx_list[i]) / period

    return round(adx, 1), round(adx_prev, 1) if adx_prev is not None else None


def calculate_rsi_from_bars(bars, period=14):
    """
    Berechnet RSI aus OHLC-Bars.
    Returns: RSI-Wert (0-100) oder None
    """
    if not bars or len(bars) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(bars)):
        change = bars[i]["close"] - bars[i-1]["close"]
        gains.append(max(0, change))
        losses.append(max(0, -change))

    if len(gains) < period:
        return None

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0 and avg_gain == 0:
        return 50.0  # Neutral — keine Bewegung
    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 1)


def calculate_macd(bars, fast=12, slow=26, signal=9):
    """MACD berechnen (Standard 12/26/9). Returns (macd_line, signal_line, histogram) oder (None,None,None)."""
    if not bars or len(bars) < slow + signal:
        return None, None, None
    closes = [b["close"] for b in bars]
    # EMA Berechnung
    def _ema(data, period):
        ema = [data[0]]
        k = 2 / (period + 1)
        for i in range(1, len(data)):
            ema.append(data[i] * k + ema[-1] * (1 - k))
        return ema
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    signal_line = _ema(macd_line[slow-1:], signal)  # Signal ab slow-1
    # Histogram = MACD - Signal (nur für die letzten signal Perioden)
    offset = slow - 1
    hist = [macd_line[offset + i] - signal_line[i] for i in range(len(signal_line))]
    return macd_line[-1], signal_line[-1], hist


def calculate_stochastic(bars, k_period=14, d_period=3):
    """Stochastic Oscillator (%K, %D). Returns (k, d) oder (None, None)."""
    if not bars or len(bars) < k_period + d_period:
        return None, None
    # %K für jeden Bar
    k_values = []
    for i in range(k_period - 1, len(bars)):
        window = bars[i - k_period + 1:i + 1]
        highest = max(b["high"] for b in window)
        lowest = min(b["low"] for b in window)
        if highest == lowest:
            k_values.append(50.0)
        else:
            k_values.append(((bars[i]["close"] - lowest) / (highest - lowest)) * 100)
    # %D = SMA von %K
    if len(k_values) < d_period:
        return None, None
    d_values = []
    for i in range(d_period - 1, len(k_values)):
        d_values.append(sum(k_values[i - d_period + 1:i + 1]) / d_period)
    return round(k_values[-1], 1), round(d_values[-1], 1)


def calculate_sma(closes, period):
    """
    Berechnet Simple Moving Average.

    Args:
        closes: Liste von Schlusskursen (neuester zuletzt)
        period: SMA Periode (z.B. 50, 200)

    Returns:
        SMA Wert oder None wenn nicht genug Daten
    """
    if not closes or len(closes) < period:
        return None

    # Nimm die letzten 'period' Werte
    relevant_closes = closes[-period:]
    return sum(relevant_closes) / period


def calculate_ema(closes, period):
    """
    Berechnet Exponential Moving Average.

    Args:
        closes: Liste von Schlusskursen (neuester zuletzt)
        period: EMA Periode (z.B. 8, 21)

    Returns:
        EMA Wert oder None wenn nicht genug Daten
    """
    if not closes or len(closes) < period:
        return None

    multiplier = 2 / (period + 1)

    # Starte mit SMA als Basis
    ema = sum(closes[:period]) / period

    # Berechne EMA für restliche Werte
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema

    return ema


def calculate_ma_distance(price, ma_value):
    """
    Berechnet den Abstand vom Preis zum Moving Average in Prozent.

    Positiv = Preis über MA
    Negativ = Preis unter MA
    """
    if not ma_value or ma_value <= 0:
        return None

    return ((price - ma_value) / ma_value) * 100


def calculate_vwap(ohlcv_data):
    """
    Berechnet VWAP (Volume Weighted Average Price) mit Standard Deviations.

    Returns:
        dict mit vwap, upper_band_1, upper_band_2, lower_band_1, lower_band_2
    """
    if not ohlcv_data or len(ohlcv_data) < 5:
        return None

    try:
        # Typischer Preis = (High + Low + Close) / 3
        typical_prices = [(d["high"] + d["low"] + d["close"]) / 3 for d in ohlcv_data]
        volumes = [d.get("volume", 0) for d in ohlcv_data]

        # Kumulative Werte
        cumulative_tp_vol = 0
        cumulative_vol = 0
        vwap_values = []

        for tp, vol in zip(typical_prices, volumes):
            cumulative_tp_vol += tp * vol
            cumulative_vol += vol
            if cumulative_vol > 0:
                vwap_values.append(cumulative_tp_vol / cumulative_vol)
            else:
                vwap_values.append(tp)

        current_vwap = vwap_values[-1] if vwap_values else typical_prices[-1]

        # Standard Deviation berechnen
        squared_diffs = [(tp - current_vwap) ** 2 for tp in typical_prices]
        variance = sum(squared_diffs) / len(squared_diffs)
        std_dev = variance ** 0.5

        return {
            "vwap": round(current_vwap, 2),
            "vwap_values": vwap_values,
            "std_dev": round(std_dev, 2),
            "upper_1": round(current_vwap + std_dev, 2),
            "upper_2": round(current_vwap + 2 * std_dev, 2),
            "lower_1": round(current_vwap - std_dev, 2),
            "lower_2": round(current_vwap - 2 * std_dev, 2),
        }
    except Exception as e:
        return None


def calculate_ema_series(data, period):
    """Berechnet EMA-Serie für eine Liste von Werten. Returns Liste gleicher Länge (None für ungenügend Daten)."""
    if not data or period <= 0:
        return [None] * len(data)
    result = [None] * len(data)
    multiplier = 2 / (period + 1)
    # Seed: SMA der ersten 'period' Werte
    if len(data) < period:
        return result
    sma = sum(data[:period]) / period
    result[period - 1] = sma
    for i in range(period, len(data)):
        result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def calculate_obv(closes, volumes):
    """
    Berechnet On-Balance-Volume (OBV)
    OBV steigt wenn Smart Money akkumuliert
    """
    if not closes or not volumes or len(closes) != len(volumes):
        return [], 0

    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])

    # OBV Trend (Steigung der letzten 10 Perioden)
    if len(obv) >= 10:
        recent_obv = obv[-10:]
        trend = (recent_obv[-1] - recent_obv[0]) / (abs(recent_obv[0]) + 1) * 100
    else:
        trend = 0

    return obv, trend
