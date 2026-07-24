"""
Scoring Functions Module — Breakout Health, Confluence, Alpha, Setup, Exhaustion Quality Scoring.

Extracted from scanner.py v68.0 to modularize scoring logic.
"""

import math

from modules.indicators import (
    _mcap_atr_baseline as _indicators_mcap_atr_baseline,
    calculate_close_position as _indicators_close_position,
    estimate_crypto_atr as _indicators_estimate_crypto_atr,
)

# Catalyst Constants
BEARISH_CATALYSTS = {" OFFERING", " LEGAL", " DOWNGRADE", " BANKRUPTCY", " REVERSE SPLIT"}
BULLISH_CATALYSTS = {" M&A", " CONTRACT", " UPGRADE", " DIVIDEND", " INSIDER", " PRODUCT", " STOCK SPLIT"}


# FIX 5: Pearson Correlation Helper
def _pearson_corr(x_list, y_list):
    """Calculate Pearson correlation coefficient.

    Returns correlation between -1.0 and 1.0.
    Returns 0.0 if insufficient data (< 5 points).
    """
    n = min(len(x_list), len(y_list))
    if n < 5:
        return 0.0
    x = x_list[-n:]
    y = y_list[-n:]
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    den_x = math.sqrt(sum((xi - mean_x)**2 for xi in x))
    den_y = math.sqrt(sum((yi - mean_y)**2 for yi in y))
    if den_x * den_y == 0:
        return 0.0
    return num / (den_x * den_y)


# FIX 6: Z-Score Helper
def _z_score(value, values_list):
    """Calculate z-score for statistical significance.

    Returns 0.0 if insufficient data (< 5 points).
    Z-score >= 2.0 is treated as significant. AUDIT 2026-07-24: the textbook
    "95% confidence" holds only under normality; hourly crypto returns are
    fat-tailed (empirically ~90%). The threshold is a robust heuristic gate,
    not a calibrated statistical test.
    """
    if len(values_list) < 5:
        return 0.0
    mean = sum(values_list) / len(values_list)
    variance = sum((x - mean)**2 for x in values_list) / len(values_list)
    stdev = math.sqrt(variance) if variance > 0 else 1.0
    return (value - mean) / stdev


def _mcap_atr_baseline(market_cap):
    """H-13: Delegiert an modules.indicators._mcap_atr_baseline (kanonische Tiers)."""
    return _indicators_mcap_atr_baseline(market_cap)


def estimate_crypto_atr(market_cap, high_24h=None, low_24h=None, price=None):
    """H-13: Delegiert an modules.indicators.estimate_crypto_atr.

    Kanonisch ist die Variante MIT Pump-Kappung (Range > 2x MCap-Baseline
    -> Baseline), implementiert in modules/indicators.py. Frueher gab es hier
    ein abweichendes Duplikat; jetzt gibt es genau eine Implementierung.
    """
    return _indicators_estimate_crypto_atr(market_cap, high_24h, low_24h, price)


def detect_chart_patterns(ohlcv_data, lookback=50):
    """H-13: Delegiert an die echte Implementierung modules.patterns.detect_chart_patterns.

    Lazy-Import in der Funktion, damit der schwere patterns-Import
    (requests, data_fetchers, volume_analysis) nicht beim scorers-Import
    haengt und keine Zirkular-Import-Kette entstehen kann.
    """
    from modules.patterns import detect_chart_patterns as _full_detect_chart_patterns

    return _full_detect_chart_patterns(ohlcv_data, lookback=lookback)


def calculate_close_position(high, low, close, min_range_pct=1.0):
    """H-13: Delegiert an modules.indicators.calculate_close_position.

    Die kanonische Version clampt auf 0-1, liefert None bei fehlender/zu
    kleiner Range und respektiert min_range_pct (das alte Duplikat hier
    ignorierte min_range_pct und gab ungeclampte Werte zurueck).
    """
    return _indicators_close_position(high, low, close, min_range_pct=min_range_pct)


def assess_breakout_health(change_pct, rvol, close_pos, high, low, close,
                           open_price=None, prev_close=None, prev_high=None,
                           prev_low=None, vortag_pct=None, vi_result=None,
                           atr_pct=None, market_type="Aktien", market_cap=None):
    """
    Bewertet die Gesundheit eines Breakouts auf einer Skala von 0-100.

    ARCHITEKTUR:
      1. Candle Structure (Wick + Body) — Hauptindikator für Fakeout
      2. Volume Confirmation (RVOL) — Institutionelles Interesse
      3. Extension vs. Volatility (Change/ATR) — Überdehnung relativ
      4. Context (Vortag, VI Zones) — Woher kommt der Breakout?
      5. Selloff Risk — integriert in Health Score, nicht separat

    WICHTIG: Close Position wird NICHT separat gewertet, weil sie
    mathematisch redundant mit Wick-Analyse ist (beides misst wo
    der Close relativ zum High/Low liegt).

    Args:
        change_pct: Prozentuale Veränderung (muss > 0 für Breakout Long)
        rvol: Relative Volume (None wenn nicht verfügbar)
        close_pos: Close Position 0-1 (redundant mit Wick, nur für Display)
        high, low, close: Preis-Daten der aktuellen Kerze
        open_price: Open der aktuellen Kerze (None wenn nicht verfügbar)
        prev_close: Vortags-Close (für Gap-Berechnung)
        prev_high, prev_low: Vortags-High/Low (für Level-Analyse)
        vortag_pct: Vortags-Veränderung in % (für Multi-Day Kontext)
        vi_result: Volume Imbalance Ergebnis (für Resistance-Zonen)
        atr_pct: Average True Range in % (für Volatilitäts-Normalisierung)

    Returns:
        dict mit health_score, verdict, warnings, signals, selloff_risk, action
    """
    if change_pct is None or change_pct <= 0:
        return None

    health = 50  # Neutral starten
    warnings = []
    signals = []
    has_volume_data = rvol is not None
    multi_day_thresh = 1.5 if market_type == "Krypto" else 3.0
    is_multi_day_run = vortag_pct is not None and vortag_pct > multi_day_thresh

    # ================================================================
    # 1. CANDLE STRUCTURE — Wick + Body (HAUPTINDIKATOR)
    #    Misst: Verkaufsdruck, Conviction, Fakeout-Wahrscheinlichkeit
    #    ERSETZT Close Position (redundant) — kein Double Counting
    # ================================================================
    candle_analyzed = False
    upper_wick_pct = 0
    body_pct = 0

    if high and low and close and high > low:
        total_range = high - low

        # Body-Grenzen bestimmen
        if open_price and open_price > 0:
            # Echtes Open vorhanden — beste Daten
            body_top = max(open_price, close)
            body_bot = min(open_price, close)
        elif prev_close and prev_close > 0:
            # Open nicht vorhanden, prev_close als Proxy
            # KRITISCH: Wenn prev_close AUSSERHALB [Low, High] liegt,
            # gab es einen Gap. Dann ist prev_close KEIN guter Open-Proxy,
            # weil wir nicht wissen wo der Kurs wirklich eröffnet hat.
            if low <= prev_close <= high:
                # Prev Close liegt im heutigen Range → brauchbarer Proxy
                estimated_open = prev_close
                body_top = max(estimated_open, close)
                body_bot = min(estimated_open, close)
            else:
                # V68: Gap — Midpoint als konservative Open-Schätzung
                # Alte Logik erzeugte Doji bei close < midpoint (body_bot = close = body_top)
                # Neue Logik: Midpoint als Open → realistischere Body-Einschätzung
                estimated_open = (high + low) / 2
                body_top = max(estimated_open, close)
                body_bot = min(estimated_open, close)
                warnings.append(f" Gap erkannt (PrevClose ${prev_close:.2f} außerhalb Range ${low:.2f}-${high:.2f}) — Kerzen-Analyse geschätzt")
        else:
            # Weder Open noch PrevClose — nur High/Low/Close nutzen
            body_top = close
            body_bot = low

        # Sicherheits-Clamp: Body kann nie größer als Kerze sein
        body_top = min(body_top, high)
        body_bot = max(body_bot, low)

        # Ensure body_bot <= body_top after clamping
        if body_bot > body_top:
            body_bot, body_top = body_top, body_bot

        body_size = body_top - body_bot
        upper_wick = max(0, high - body_top)
        lower_wick = max(0, body_bot - low)

        upper_wick_pct = upper_wick / total_range if total_range > 0 else 0
        body_pct = body_size / total_range if total_range > 0 else 0
        lower_wick_pct = lower_wick / total_range if total_range > 0 else 0
        candle_analyzed = True

        # --- Upper Wick (Verkaufsdruck) ---
        if upper_wick_pct > 0.45:
            health -= 20
            warnings.append(f" Extremer Docht ({upper_wick_pct:.0%} der Range) — massiver Verkaufsdruck!")
        elif upper_wick_pct > 0.30:
            health -= 12
            warnings.append(f" Langer Docht ({upper_wick_pct:.0%}) — Verkäufer drücken stark vom High")
        elif upper_wick_pct > 0.20:
            health -= 5
            warnings.append(f" Oberer Docht ({upper_wick_pct:.0%}) — leichter Verkaufsdruck")
        elif upper_wick_pct < 0.08:
            health += 8
            signals.append(f" Kaum Docht ({upper_wick_pct:.0%}) — kein Verkaufsdruck")
        else:
            health += 3
            signals.append(f" Normaler Docht ({upper_wick_pct:.0%})")

        # --- Body Size (Conviction) ---
        # KRITISCH: Body-RICHTUNG zählt! Ein großer BEARISHER Body
        # (Close < Open) bei einem Breakout Long = Verkaufsdruck, NICHT Überzeugung!
        is_bullish_candle = close >= body_bot + body_size * 0.5  # Close in oberer Hälfte
        if open_price and open_price > 0:
            is_bullish_candle = close > open_price

        if is_bullish_candle:
            if body_pct > 0.75:
                health += 8
                signals.append(f" Großer bullisher Body ({body_pct:.0%}) — starke Überzeugung")
            elif body_pct > 0.55:
                health += 4
                signals.append(f" Solider bullisher Body ({body_pct:.0%})")
            elif body_pct < 0.20:
                health -= 12
                warnings.append(f" Doji ({body_pct:.0%} Body) — totale Unentschlossenheit")
            elif body_pct < 0.35:
                health -= 5
                warnings.append(f" Kleiner Body ({body_pct:.0%}) — schwache Conviction")
        else:
            # BEARISH Kerze bei Long-Breakout = Warnung!
            if body_pct > 0.50:
                health -= 12
                warnings.append(f" Großer bearisher Body ({body_pct:.0%}) — Verkaufsdruck trotz Gap-Up!")
            elif body_pct > 0.30:
                health -= 5
                warnings.append(f" Bearisher Body ({body_pct:.0%}) — Käufer verlieren Kontrolle")
            elif body_pct < 0.20:
                health -= 12
                warnings.append(f" Doji ({body_pct:.0%} Body) — totale Unentschlossenheit")

        # --- Lower Wick (Buying Support) ---
        # Ein langer Lower Wick bei einem Breakout = Käufer fingen den Dip auf
        if lower_wick_pct > 0.25 and upper_wick_pct < 0.15:
            health += 3
            signals.append(f" Langer unterer Docht ({lower_wick_pct:.0%}) — Käufer verteidigten das Low")

    # ================================================================
    # 2. VOLUME CONFIRMATION — Institutionelles Interesse
    # ================================================================
    if has_volume_data:
        if rvol >= 3.0:
            health += 15
            signals.append(f" Starkes Volume ({rvol:.1f}x) — institutionell getrieben")
        elif rvol >= 2.0:
            health += 10
            signals.append(f" Gutes Volume ({rvol:.1f}x) — bestätigt")
        elif rvol >= 1.5:
            health += 5
            signals.append(f" Akzeptables Volume ({rvol:.1f}x)")
        elif rvol >= 0.7:
            # RVOL 0.7-1.5 = neutral/unterdurchschnittlich — kein Bonus, kein Penalty
            pass
        else:
            health -= 15
            warnings.append(f" LOW VOLUME ({rvol:.1f}x) — Fakeout-Risiko HOCH!")

        # Volume Climax — NUR gefährlich wenn der Run SCHON LÄUFT
        # Tag 1 mit RVOL 7x = perfekt (institutioneller Einstieg)
        # Tag 3+ mit RVOL 7x = alle haben schon gekauft = Top
        if rvol >= 5.0 and is_multi_day_run:
            health -= 10
            warnings.append(f" Volume Climax ({rvol:.1f}x) nach mehrtägigem Run — oft das Top!")
        elif rvol >= 5.0:
            # Tag 1 = keine Strafe, nur Info
            signals.append(f"ℹ Extremes Volume ({rvol:.1f}x) — Tag 1 = gut, beobachte Folgetage")
    else:
        # Kein Volume-Daten: Score-Ceiling begrenzen (am Ende angewandt)
        warnings.append(" Kein Volume-Daten — Breakout-Qualität kann nicht vollständig bewertet werden")

    # ================================================================
    # 3. EXTENSION — Wie weit, relativ zur normalen Volatilität?
    #    ATR-normalisiert wenn verfügbar, sonst absolute Schwellen
    # ================================================================
    if atr_pct and atr_pct > 0:
        # ATR-INFLATION CHECK: Unterscheide Post-Crash (abnormal hoch)
        # von natürlich volatilen Penny Stocks.
        # Penny Stocks (<$10) haben NORMAL 5-10% ATR.
        # Midcap ($10-50) hat normal 2-5% ATR.
        # Large Cap (>$50) hat normal 0.5-2% ATR.
        atr_warning_threshold = 8.0  # Default
        atr_caution_threshold = 5.0
        if close and close > 0:
            if close < 10:
                atr_warning_threshold = 15.0  # Pennys: erst ab 15% warnen
                atr_caution_threshold = 10.0
            elif close < 50:
                atr_warning_threshold = 10.0
                atr_caution_threshold = 6.0

        if atr_pct > atr_warning_threshold:
            health -= 10
            warnings.append(f" Extrem volatile Phase (ATR {atr_pct:.1f}%) — Post-Spike/Crash, erhöhtes Risiko!")
        elif atr_pct > atr_caution_threshold:
            health -= 5
            warnings.append(f" Hohe Volatilität (ATR {atr_pct:.1f}%) — vorsichtig agieren")

        # ATR-normalisiert
        extension_ratio = change_pct / atr_pct

        if extension_ratio > 5.0:
            health -= 15
            warnings.append(f" Extrem überdehnt ({extension_ratio:.1f}x ATR) — Reversion SEHR wahrscheinlich")
        elif extension_ratio > 3.0:
            health -= 8
            warnings.append(f" Überdehnt ({extension_ratio:.1f}x ATR) — Pullback wahrscheinlich")
        elif extension_ratio > 2.0:
            health -= 3
            warnings.append(f" Ausgedehnt ({extension_ratio:.1f}x ATR)")
        elif extension_ratio >= 1.0:
            health += 3
            signals.append(f" Gesunde Extension ({extension_ratio:.1f}x ATR)")
        else:
            health -= 3
            warnings.append(f" Schwache Bewegung (nur {extension_ratio:.1f}x ATR)")

        # ABSOLUTE DISTANZ — Fängt Fälle wo ATR aufgebläht ist
        # +10% ist IMMER weit gelaufen, egal was ATR sagt
        # Ein Einstieg bei +12% hat viel schlechteres R:R als bei +3%
        if change_pct > 20:
            health -= 15
            warnings.append(f" Extreme Distanz +{change_pct:.1f}% — Einstieg hochriskant")
        elif change_pct > 15:
            health -= 10
            warnings.append(f" Absolute Distanz +{change_pct:.1f}% — weit vom Entry entfernt")
        elif change_pct > 10:
            health -= 7
            warnings.append(f" Absolute Distanz +{change_pct:.1f}% — schon weit gelaufen")
    else:
        # V69: Crypto ATR — zentrale Funktion mit echtem Range wenn verfügbar
        if market_type == "Krypto" and market_cap and market_cap > 0:
            est_atr = estimate_crypto_atr(market_cap, high, low, close)
            # V68: Guard gegen Division-by-Zero wenn est_atr = 0 (flat price)
            ext_r = change_pct / est_atr if est_atr and est_atr > 0 else 0
            if ext_r > 4.0:
                health -= 12
                warnings.append(f" Überdehnt ({ext_r:.1f}x est.ATR)")
            elif ext_r > 2.5:
                health -= 6
                warnings.append(f" Ausgedehnt ({ext_r:.1f}x est.ATR)")
            elif ext_r >= 0.7:
                health += 3
                signals.append(f" Gesunde Extension ({ext_r:.1f}x est.ATR)")
            else:
                signals.append(f"ℹ Moderate Bewegung ({ext_r:.1f}x est.ATR)")
        else:
            if change_pct > 20:
                health -= 12
                warnings.append(f" Stark überdehnt (+{change_pct:.1f}%)")
            elif change_pct > 12:
                health -= 6
                warnings.append(f" Überdehnt (+{change_pct:.1f}%)")
            elif change_pct >= 3:
                health += 3
                signals.append(f" Gesunde Breakout-Grösse (+{change_pct:.1f}%)")
            else:
                signals.append(f"ℹ Moderate Bewegung (+{change_pct:.1f}%)")

    # ================================================================
    # 4. CONTEXT — Woher kommt der Breakout?
    # ================================================================
    if vortag_pct is not None:
        if market_type == "Krypto":
            if -0.5 <= vortag_pct <= 0.5:
                health += 8
                signals.append(f" Breakout aus ruhiger Phase ({vortag_pct:+.1f}%/Tag avg)")
            elif vortag_pct < -1.5:
                warnings.append(f" Reversal nach Abwärtstrend ({vortag_pct:+.1f}%/Tag)")
            elif vortag_pct > 2.5:
                health -= 8
                warnings.append(f" Heisser Trend ({vortag_pct:+.1f}%/Tag) — Erschöpfung")
            elif vortag_pct > 1.0:
                health -= 2
                warnings.append(f" Continuation ({vortag_pct:+.1f}%/Tag)")
        else:
            vol_threshold = 6.0
            if close and close > 0 and close < 10:
                vol_threshold = 12.0
            elif close and close > 0 and close < 50:
                vol_threshold = 8.0
            is_high_vol_regime = atr_pct and atr_pct > vol_threshold
            if -2.0 <= vortag_pct <= 2.0:
                if is_high_vol_regime:
                    # V68: Konsolidierung in High-Vol = Akkumulation (Wyckoff)
                    health += 5
                    signals.append(f" Akkumulation in volatiler Phase (Vortag {vortag_pct:+.1f}%, ATR {atr_pct:.1f}%) — Stabilisierung")
                else:
                    health += 8
                    signals.append(f" Breakout aus Konsolidierung (Vortag {vortag_pct:+.1f}%) — bestes Setup")
            elif vortag_pct < -3.0:
                warnings.append(f" Reversal-Breakout (Vortag {vortag_pct:+.1f}%) — kann Bounce sein")
            elif vortag_pct > 5.0:
                health -= 8
                warnings.append(f" Multi-Day Run (Vortag {vortag_pct:+.1f}%) — Erschöpfung nähert sich")
            elif vortag_pct > 2.0:
                health -= 2
                warnings.append(f" Continuation (Vortag {vortag_pct:+.1f}%) — Trend läuft schon")

    # ================================================================
    # 5. VOLUME IMBALANCE CONFLUENCE — Resistance voraus?
    # ================================================================
    if vi_result and close:
        unfilled_bear = vi_result.get("unfilled_bear", [])
        if unfilled_bear:
            nearest = unfilled_bear[0]
            dist = (nearest["zone_low"] - close) / close * 100 if close > 0 else 0

            if dist < 1.0:
                health -= 10
                warnings.append(f" Bearish {nearest['type']} nur {dist:.1f}% entfernt "
                               f"(${nearest['zone_low']:.2f}-${nearest['zone_high']:.2f}) — Resistance!")
            elif dist < 3.0:
                health -= 5
                warnings.append(f" Bearish {nearest['type']} {dist:.1f}% entfernt — potentielle Resistance")
            else:
                health += 3
                signals.append(f" Keine nahe Resistance ({dist:.1f}% bis nächste Zone)")
        else:
            health += 3
            signals.append(" Keine unfilled Bearish Zones — freier Weg nach oben")

    # ================================================================
    # 6. SELLOFF RISK — Integriert in Health (kein separater Score)
    #    Basiert auf: Multi-Day Extension + Volume Climax + Wick Wachstum
    # ================================================================
    selloff_pressure = 0

    # Overextension + Multi-Day = höchstes Selloff-Risiko
    if is_multi_day_run:
        selloff_pressure += 15
        if change_pct > 8:
            selloff_pressure += 15  # Noch ein starker Tag nach starkem Vortag
        if has_volume_data and rvol and rvol > 5.0:
            selloff_pressure += 20  # Volume Climax nach Run = Top-Signal

    # Candle zeigt Schwäche (NUR body_pct — Upper Wick wird bereits oben bei health direkt bestraft)
    if candle_analyzed:
        # upper_wick_pct wird NICHT hier geprüft → Doppel-Bestrafung vermeiden (Z.204-208 deckt das ab)
        if body_pct < 0.25:
            selloff_pressure += 10  # Kein Momentum mehr

    # Overextension (relativ zu ATR)
    if atr_pct and atr_pct > 0 and change_pct / atr_pct > 4.0:
        selloff_pressure += 15
    elif change_pct > 15:  # Absolute Fallback
        selloff_pressure += 15

    # Selloff-Pressure reduziert Health
    health -= int(selloff_pressure * 0.3)  # 30% des Selloff-Drucks fließt in Health

    # ================================================================
    # ERGEBNIS
    # ================================================================
    health = max(0, min(100, health))

    # Volume-Cap: Ohne Volume-Daten max 65 (Volume ist der wichtigste Indikator)
    if not has_volume_data:
        health = min(health, 65)

    # Verdict — EXHAUSTION und FAKEOUT unterscheiden
    # EXHAUSTION = Breakout war echt, aber zu weit gelaufen (Multi-Day)
    # FAKEOUT = Breakout war nie echt (Low Vol, Wick Rejection)
    is_exhaustion = is_multi_day_run and selloff_pressure >= 30

    if health >= 75:
        verdict = "STRONG"
        verdict_emoji = "[**][+]"
    elif health >= 55:
        verdict = "HEALTHY"
        verdict_emoji = "[OK][+]"
    elif health >= 40:
        verdict = "CAUTION"
        verdict_emoji = "[!][~]"
    elif health >= 25:
        if is_exhaustion:
            verdict = "EXHAUSTED"
            verdict_emoji = "[!]"
        else:
            verdict = "WEAK"
            verdict_emoji = "[!][!]"
    else:
        if is_exhaustion:
            verdict = "EXHAUSTED"
            verdict_emoji = "[-]"
        else:
            verdict = "FAKEOUT"
            verdict_emoji = "[X][-]"

    # Selloff Risk Label
    if selloff_pressure >= 45:
        selloff_risk = "IMMINENT"
        selloff_emoji = "[!!]"
    elif selloff_pressure >= 25:
        selloff_risk = "HIGH"
        selloff_emoji = "[-]"
    elif selloff_pressure >= 10:
        selloff_risk = "MEDIUM"
        selloff_emoji = "[~]"
    else:
        selloff_risk = "LOW"
        selloff_emoji = "[+]"

    # Action
    if verdict == "FAKEOUT":
        action = "EXIT — Breakout ist wahrscheinlich nicht echt. Sofort raus oder eng absichern."
    elif verdict == "EXHAUSTED" or selloff_risk == "IMMINENT":
        action = "TAKE PROFIT — Run ist erschöpft/Selloff steht bevor. Gewinne sichern, Trailing Stop eng."
    elif selloff_risk == "HIGH":
        action = "TIGHTEN STOP — Stop auf Breakeven oder knapp darunter ziehen."
    elif verdict == "WEAK":
        action = "REDUCE SIZE — Position verkleinern, nicht nachlegen."
    elif verdict == "CAUTION":
        action = "HOLD MIT STOP — Halten, aber Stop-Loss nicht vergessen."
    elif verdict == "STRONG":
        action = "HOLD / ADD — Starker Breakout, Pyramidisieren bei Pullback möglich."
    else:
        action = "HOLD — Breakout sieht gesund aus. Stop unter Tageslow."

    return {
        "health_score": health,
        "verdict": verdict,
        "verdict_emoji": verdict_emoji,
        "selloff_risk": selloff_risk,
        "selloff_risk_emoji": selloff_emoji,
        "selloff_pressure": selloff_pressure,
        "warnings": warnings,
        "signals": signals,
        "action": action,
        "details": {
            "change_pct": change_pct,
            "rvol": rvol,
            "close_position": close_pos,
            "vortag_pct": vortag_pct,
            "atr_pct": atr_pct,
            "upper_wick_pct": round(upper_wick_pct, 2) if candle_analyzed else None,
            "body_pct": round(body_pct, 2) if candle_analyzed else None,
        }
    }


def calculate_confluence_score(ticker, price, change_pct, rvol, close_pos,
                                high, low, prev_close, vortag_pct, atr_pct,
                                dollar_volume, ohlcv_data=None,
                                spy_change=None, spy_trend_bullish=None,
                                direction="long"):
    """
    10-Kategorie Confluence Engine — SUPER SIGNAL Erkennung.

    Jede Kategorie gibt PASS () oder FAIL ().
    Nur wenn genug Kategorien PASS → Signal.

    KATEGORIEN (alle UNABHÄNGIG voneinander):
    ─────────────────────────────────────────────────────
    1. VOLUME          — Institutionelles Interesse (RVOL)
    2. KERZE           — Fakeout oder echte Überzeugung
    3. TREND           — EMAs gestapelt/alignt?
    4. TIMING          — Zu spät oder noch früh?
    5. PATTERN         — Chart-Pattern, Wyckoff, Base
    6. MARKT-REGIME    — SPY/QQQ kauft oder crashed?
    7. RELATIVE STÄRKE — Outperformt Aktie den Markt?
    8. LIQUIDITÄT      — Genug Volumen zum traden?
    9. RESISTANCE      — Freiraum nach oben/unten?
    10. MULTI-TIMEFRAME — Stimmt Weekly mit Daily überein?
    ─────────────────────────────────────────────────────

    Args:
        Basic scan data (from Polygon snapshot)
        ohlcv_data: OHLCV history für tiefere Analyse (optional)
        spy_change: SPY Tages-Change% (für Markt-Regime)
        spy_trend_bullish: SPY über EMA50? (None wenn unbekannt)
        direction: "long" oder "short"

    Returns:
        dict mit categories (10x PASS/FAIL), total_score, signal, details
    """
    categories = {}
    is_long = direction == "long"
    abs_change = abs(change_pct) if change_pct else 0

    # ================================================================
    # 1. VOLUME CONVICTION — Kaufen/Verkaufen Institutionen?
    # ================================================================
    if rvol is not None and rvol > 0:
        if is_long:
            vol_pass = rvol >= 1.8
        else:
            vol_pass = rvol >= 1.5  # Shorts brauchen weniger RVOL

        categories["volume"] = {
            "name": "Volume",
            "emoji": "",
            "pass": vol_pass,
            "value": f"RVOL {rvol:.1f}x",
            "detail": "Institutionell" if rvol >= 3.0 else "Bestätigt" if vol_pass else "Schwach"
        }
    else:
        categories["volume"] = {
            "name": "Volume",
            "emoji": "",
            "pass": False,
            "value": "N/A",
            "detail": "Keine Daten"
        }

    # ================================================================
    # 2. KERZEN-QUALITÄT — Fakeout oder echt?
    # ================================================================
    candle_pass = False
    candle_detail = "Keine Daten"

    if high and low and high > low:
        total_range = high - low
        close_in_range = (price - low) / total_range if total_range > 0 else 0.5

        if is_long:
            # Long: Close sollte nahe High sein (>60%), Wick < 30%
            upper_wick_pct = (high - max(price, prev_close if prev_close and low <= prev_close <= high else price)) / total_range if total_range > 0 else 0
            upper_wick_pct = max(0, min(1, upper_wick_pct))
            candle_pass = close_in_range >= 0.60 and upper_wick_pct < 0.30
            candle_detail = f"Close {close_in_range:.0%} | Wick {upper_wick_pct:.0%}"
        else:
            # Short: Close sollte nahe Low sein (<40%)
            # UND: Lower Wick darf NICHT zu groß sein (Hammer = bullish rejection)
            lower_wick_pct = (min(price, prev_close if prev_close and low <= prev_close <= high else price) - low) / total_range if total_range > 0 else 0
            lower_wick_pct = max(0, min(1, lower_wick_pct))
            candle_pass = close_in_range <= 0.40 and lower_wick_pct < 0.30
            candle_detail = f"Close {close_in_range:.0%} | Lower Wick {lower_wick_pct:.0%}"

    categories["candle"] = {
        "name": "Kerze",
        "emoji": "",
        "pass": candle_pass,
        "value": candle_detail,
        "detail": "Stark" if candle_pass else "Schwach/Fakeout"
    }

    # ================================================================
    # 3. TREND ALIGNMENT — EMAs gestapelt?
    # ================================================================
    trend_pass = False
    trend_detail = "Keine History"

    if ohlcv_data and len(ohlcv_data) >= 50:
        closes = [d.get("close", d.get("c", 0)) for d in ohlcv_data]
        # Verwende letzten OHLCV-Close für EMA-Vergleich (konsistent!)
        # Der extern übergebene `price` kann ein Snapshot sein der abweicht
        current_price = closes[-1]

        # EMA Berechnung
        def calc_ema(data, period):
            if len(data) < period:
                return None
            ema = sum(data[:period]) / period
            mult = 2 / (period + 1)
            for val in data[period:]:
                ema = (val - ema) * mult + ema
            return ema

        ema20 = calc_ema(closes, 20)
        ema50 = calc_ema(closes, 50)
        ema200 = calc_ema(closes, 200) if len(closes) >= 200 else None

        if ema20 and ema50:
            if is_long:
                # Long: Price > EMA20 > EMA50 (und optional > EMA200)
                price_above_20 = current_price > ema20
                ema20_above_50 = ema20 > ema50
                if ema200:
                    trend_pass = price_above_20 and ema20_above_50 and ema50 > ema200
                    trend_detail = f"{'' if trend_pass else ''} P>{('>' if ema20_above_50 else '<')}EMA20{('>' if ema20_above_50 else '<')}EMA50{('>' if ema50 > ema200 else '<') if ema200 else ''}{('EMA200' if ema200 else '')}"
                else:
                    trend_pass = price_above_20 and ema20_above_50
                    trend_detail = f"EMA20 {'>' if ema20_above_50 else '<'} EMA50 (kein EMA200)"
            else:
                # Short: Price < EMA20 < EMA50
                price_below_20 = current_price < ema20
                ema20_below_50 = ema20 < ema50
                if ema200:
                    trend_pass = price_below_20 and ema20_below_50 and ema50 < ema200
                else:
                    trend_pass = price_below_20 and ema20_below_50
                trend_detail = f"EMA20 {'<' if ema20_below_50 else '>'} EMA50 {'(bearish)' if trend_pass else '(nicht alignt)'}"

    categories["trend"] = {
        "name": "Trend",
        "emoji": "" if is_long else "",
        "pass": trend_pass,
        "value": trend_detail,
        "detail": "Alignt" if trend_pass else "Nicht alignt"
    }

    # ================================================================
    # 4. TIMING / EXTENSION — Zu spät oder noch früh?
    # ================================================================
    timing_pass = False

    if atr_pct and atr_pct > 0:
        extension = abs_change / atr_pct
        # V68: ATR-normalisiert + ATR-skalierter Absolut-Check
        # Alte Logik: abs_change < 8% (hardcoded) bestrafte High-ATR Aktien
        # Neue Logik: Ceiling skaliert mit ATR → Penny Stocks mit 10% ATR dürfen +12% bewegen
        atr_ok = extension < 3.5
        abs_ceiling = max(8.0, 3.5 * atr_pct)  # Dynamisch statt hardcoded 8%
        abs_ok = abs_change < abs_ceiling
        timing_pass = atr_ok and abs_ok
        timing_detail = f"{extension:.1f}x ATR | {abs_change:.1f}%"
        if not atr_ok:
            timing_detail += " (ATR überdehnt)"
        elif not abs_ok:
            timing_detail += " (absolut zu weit)"
    else:
        timing_pass = abs_change < 10
        timing_detail = f"{abs_change:.1f}% (kein ATR)"

    categories["timing"] = {
        "name": "Timing",
        "emoji": "",
        "pass": timing_pass,
        "value": timing_detail,
        "detail": "Noch früh" if timing_pass else "Zu spät"
    }

    # ================================================================
    # 5. PATTERN / STRUKTUR — Technische Basis vorhanden?
    # ================================================================
    pattern_pass = False
    pattern_detail = "Keine History"
    pattern_names = []

    if ohlcv_data and len(ohlcv_data) >= 30:
        patterns = detect_chart_patterns(ohlcv_data, lookback=min(80, len(ohlcv_data)))

        if is_long:
            # NUR bullish Patterns! "neutral" (Doji) ist KEINE Bestätigung
            directional_patterns = [p for p in patterns if p.get("type") == "bullish"]
        else:
            directional_patterns = [p for p in patterns if p.get("type") == "bearish"]

        pattern_pass = len(directional_patterns) > 0
        pattern_names = [p.get("pattern", "?") for p in directional_patterns[:3]]
        pattern_detail = ", ".join(pattern_names) if pattern_names else "Kein Pattern"

    categories["pattern"] = {
        "name": "Pattern",
        "emoji": "",
        "pass": pattern_pass,
        "value": pattern_detail,
        "detail": "Bestätigt" if pattern_pass else "Kein Setup"
    }

    # ================================================================
    # 6. MARKT-REGIME — SPY kauft oder crashed?
    # ================================================================
    market_pass = False

    if spy_change is not None:
        if is_long:
            # Long: SPY nicht im Crash (>-1.5%)
            market_pass = spy_change > -1.5
        else:
            # Short: SPY nicht in Rally (<+1.5%)
            market_pass = spy_change < 1.5

        # V68: SPY Trend Override — NUR im echten Crash-Modus (<-3%)
        # Bei normalen Korrektionen (-1% bis -3%) entstehen die stärksten Rebounds
        if spy_trend_bullish is not None:
            if is_long and not spy_trend_bullish and spy_change < -3.0:
                market_pass = False  # SPY im Crash-Modus — Long zu riskant
            elif not is_long and spy_trend_bullish and spy_change > 3.0:
                market_pass = False  # SPY bullish Trend UND heute grün

        market_detail = f"SPY {spy_change:+.1f}%"
    else:
        # Keine SPY-Daten = FAIL, nicht Gratis-PASS
        # Ohne Markt-Kontext fehlt eine wichtige Bestätigung
        market_pass = False
        market_detail = "N/A (keine Daten)"

    categories["market"] = {
        "name": "Markt",
        "emoji": "",
        "pass": market_pass,
        "value": market_detail,
        "detail": "Unterstützend" if market_pass else "Gegenwind"
    }

    # ================================================================
    # 7. RELATIVE STÄRKE — Outperformt die Aktie den Markt?
    # ================================================================
    rs_pass = False

    if spy_change is not None and change_pct is not None:
        if is_long:
            relative = change_pct - spy_change
            # V68: RS = reine Outperformance vs SPY (kein absoluter Filter)
            # Aktie bei -5% mit SPY -10% = +5% RS → starkes Signal in Korrekturen
            rs_pass = relative > 2.0
        else:
            relative = spy_change - change_pct
            # Short: Aktie muss schwächer sein als SPY (pure Underperformance)
            rs_pass = relative > 2.0
        rs_detail = f"{relative:+.1f}% vs SPY ({change_pct:+.1f}%)"
    else:
        # Ohne SPY: Stärkere absolute Schwelle
        if is_long:
            rs_pass = change_pct is not None and change_pct > 3.0
        else:
            rs_pass = change_pct is not None and change_pct < -3.0
        rs_detail = f"{abs_change:.1f}% absolut (kein SPY)"

    categories["rel_strength"] = {
        "name": "Rel. Stärke",
        "emoji": "",
        "pass": rs_pass,
        "value": rs_detail,
        "detail": "Outperformer" if rs_pass else "Schwach vs Markt"
    }

    # ================================================================
    # 8. LIQUIDITÄT — Genug Volumen zum traden?
    # ================================================================
    liq_pass = dollar_volume is not None and dollar_volume >= 500000

    categories["liquidity"] = {
        "name": "Liquidität",
        "emoji": "",
        "pass": liq_pass,
        "value": f"${dollar_volume/1e6:.1f}M" if dollar_volume and dollar_volume >= 1e6 else f"${dollar_volume/1e3:.0f}k" if dollar_volume else "N/A",
        "detail": "Tradeable" if liq_pass else "Zu dünn"
    }

    # ================================================================
    # 9. RESISTANCE-FREIRAUM — Ist der Weg frei?
    # ================================================================
    resistance_pass = False
    resistance_detail = "Keine History"

    if ohlcv_data and len(ohlcv_data) >= 20:
        # Einfacher Check: Ist der aktuelle Preis nahe einem Allzeit-/52W High?
        # Oder gibt es starke Resistance aus dem Volume Profile?
        recent_highs = [d.get("high", d.get("h", 0)) for d in ohlcv_data[-60:]] if len(ohlcv_data) >= 60 else [d.get("high", d.get("h", 0)) for d in ohlcv_data]
        max_high = max(recent_highs) if recent_highs else price

        if is_long:
            # Long: Preis nahe oder über dem 60-Bar-High = kein Overhead Supply
            dist_to_high = (max_high - price) / price * 100 if price > 0 else 0
            if price >= max_high * 0.97:
                resistance_pass = True
                resistance_detail = "Nahe/über 60-Bar-High — kein Overhead"
            elif dist_to_high < 5:
                resistance_pass = True
                resistance_detail = f"Nur {dist_to_high:.1f}% bis Hoch"
            else:
                resistance_detail = f"{dist_to_high:.1f}% unter Hoch — Overhead Supply"
        else:
            # Short: Preis nahe oder unter dem 60-Bar-Low
            recent_lows = [d.get("low", d.get("l", 0)) for d in ohlcv_data[-60:]] if len(ohlcv_data) >= 60 else [d.get("low", d.get("l", 0)) for d in ohlcv_data]
            min_low = min(recent_lows) if recent_lows else price
            dist_to_low = (price - min_low) / price * 100 if price > 0 else 0
            if price <= min_low * 1.03:
                resistance_pass = True
                resistance_detail = "Nahe/unter 60-Bar-Low — kein Support"
            elif dist_to_low < 5:
                resistance_pass = True
                resistance_detail = f"Nur {dist_to_low:.1f}% bis Low"
            else:
                resistance_detail = f"{dist_to_low:.1f}% über Low — Support darunter"

    categories["resistance"] = {
        "name": "Freiraum",
        "emoji": "",
        "pass": resistance_pass,
        "value": resistance_detail,
        "detail": "Frei" if resistance_pass else "Blockiert"
    }

    # ================================================================
    # 10. MULTI-TIMEFRAME — Stimmt Weekly mit Daily überein?
    # ================================================================
    mtf_pass = False
    mtf_detail = "Keine History"

    if ohlcv_data and len(ohlcv_data) >= 20:
        closes = [d.get("close", d.get("c", 0)) for d in ohlcv_data]

        # Weekly Trend: Letzte 5 Bars (= ca 1 Woche) vs. vorherige 5
        if len(closes) >= 10:
            recent_5 = sum(closes[-5:]) / 5
            prev_5 = sum(closes[-10:-5]) / 5
            weekly_trend_up = recent_5 > prev_5
            weekly_change = (recent_5 - prev_5) / prev_5 * 100 if prev_5 > 0 else 0

            # Längerfristig: Letzte 20 Bars vs vorherige 20
            if len(closes) >= 40:
                recent_20 = sum(closes[-20:]) / 20
                prev_20 = sum(closes[-40:-20]) / 20
                monthly_trend_up = recent_20 > prev_20
            else:
                monthly_trend_up = weekly_trend_up

            if is_long:
                mtf_pass = weekly_trend_up and monthly_trend_up
            else:
                mtf_pass = not weekly_trend_up and not monthly_trend_up

            mtf_detail = f"Weekly {'↑' if weekly_trend_up else '↓'} {weekly_change:+.1f}% | Monthly {'↑' if monthly_trend_up else '↓'}"

    categories["multi_tf"] = {
        "name": "Multi-TF",
        "emoji": "",
        "pass": mtf_pass,
        "value": mtf_detail,
        "detail": "Alignt" if mtf_pass else "Widerspruch"
    }

    # ================================================================
    # ERGEBNIS — Wie viele Kategorien sind PASS?
    # ================================================================
    total_pass = sum(1 for c in categories.values() if c["pass"])
    total_categories = len(categories)

    # ================================================================
    # VETO-LOGIK: Kritische Widersprüche = Kein Trade
    # Wenn Trend + Multi-TF + Pattern ALLE gegen die Richtung sind,
    # ist das ein klares Gegentrend-Signal egal was Volume/Kerze sagen.
    # ================================================================
    _trend_fail = not categories.get("trend", {}).get("pass", True)
    _mtf_fail = not categories.get("multi_tf", {}).get("pass", True)
    _pattern_fail = not categories.get("pattern", {}).get("pass", True)
    _resistance_fail = not categories.get("resistance", {}).get("pass", True)

    _structural_fails = sum([_trend_fail, _mtf_fail, _pattern_fail, _resistance_fail])
    _veto = False

    # Veto 1: Trend + Multi-TF + Pattern alle gegen uns = Gegentrend-Trade
    if _trend_fail and _mtf_fail and _pattern_fail:
        _veto = True

    # Veto 2: Trend + Multi-TF + Resistance alle gegen uns = kein Freiraum
    if _trend_fail and _mtf_fail and _resistance_fail:
        _veto = True

    # Veto 3: 3+ strukturelle Fails = zu viele Warnsignale
    if _structural_fails >= 3:
        _veto = True

    if _veto:
        # Deckele auf max 5 PASS (= "KEIN TRADE"), egal wie gut Volume/Kerze sind
        total_pass = min(total_pass, 5)

    # Signal-Level
    if total_pass >= 9:
        signal = "SUPER"
        signal_emoji = "[*][*][*]"
        signal_color = "green"
        action = f"FULL SEND {'LONG' if is_long else 'SHORT'} — {total_pass}/10 Kategorien bestätigt!"
    elif total_pass >= 8:
        signal = "STARK"
        signal_emoji = "[*][*]"
        signal_color = "green"
        action = f"STRONG {'LONG' if is_long else 'SHORT'} — {total_pass}/10 bestätigt"
    elif total_pass >= 7:
        signal = "GUT"
        signal_emoji = "[*]"
        signal_color = "yellow"
        action = f"{'LONG' if is_long else 'SHORT'} mit normalem Risk — {total_pass}/10"
    elif total_pass >= 6:
        signal = "MÖGLICH"
        signal_emoji = "[!]"
        signal_color = "orange"
        action = f"Kleine Position möglich — nur {total_pass}/10"
    else:
        signal = "KEIN TRADE"
        signal_emoji = "[X]"
        signal_color = "red"
        action = f"KEIN TRADE — nur {total_pass}/10 Kategorien"

    # Confluence Score 0-100
    confluence_score = int((total_pass / total_categories) * 100)

    return {
        "ticker": ticker,
        "direction": direction,
        "confluence_score": confluence_score,
        "total_pass": total_pass,
        "total_categories": total_categories,
        "signal": signal,
        "signal_emoji": signal_emoji,
        "signal_color": signal_color,
        "action": action,
        "categories": categories,
        "patterns_found": pattern_names,
    }


def calculate_alpha_score(rvol, vortag_pct, change_pct):
    """
    Normalisierter Alpha Score 0-100. V67.5 — Nicht-lineare Kurven.

    Gewichtung (rebalanciert):
    - RVOL (Volumen-Interesse):  max 35 Punkte  (Volume = wichtigster Indikator)
    - Change% (Heutige Staerke): max 35 Punkte
    - Vortag% (Trend-Kontext):   max 30 Punkte  (approximiert, daher weniger Gewicht)

    V67.5 FIXES:
    - Nicht-lineares Scoring: Erste 50% kommen schnell, dann Diminishing Returns
      (RVOL 2x ist VIEL wichtiger als RVOL 4x vs 5x)
    - RVOL Cap bei 8x statt 5x (Krypto hat oft hoehere Spitzen)
    - Volumen als wichtigster Faktor gewichtet (35 statt 30)
    """
    # RVOL: 0-8x → 0-35 Punkte (logarithmisch — schneller Anstieg, dann flacher)
    rvol_safe = min(max(rvol or 0, 0), 8)
    # log(1+x) normalisiert: log(9) ≈ 2.197
    rvol_score = (math.log(1 + rvol_safe) / math.log(9)) * 35

    # Change%: 0-20% → 0-35 Punkte (sqrt — moderate Moves zaehlen mehr)
    change_abs = min(abs(change_pct or 0), 20)
    # sqrt(20) ≈ 4.47
    change_score = (math.sqrt(change_abs) / math.sqrt(20)) * 35

    # Vortag%: 0-15% → 0-30 Punkte (linear — approximierte Daten, weniger Gewicht)
    vortag_abs = min(abs(vortag_pct or 0), 15)
    vortag_score = (vortag_abs / 15) * 30

    return round(rvol_score + vortag_score + change_score, 0)


def calculate_setup_score(change_pct, rvol, close_pos, upper_wick_pct, lower_wick_pct,
                           vortag_pct, atr_pct, dollar_volume, price, direction="long"):
    """
    Setup Quality Score 0-100 — Wie gut ist dieses Setup zum Einstieg?

    Berechnet aus Snapshot-Daten (KEIN OHLCV nötig = sofort für alle Ergebnisse).
    Wird für die Sortierung verwendet: Bestes Setup zuerst.

    KATEGORIEN (aus Snapshot):
    ─────────────────────────────────────────────────────
    1. VOLUME (0-20)      — RVOL: Institutionelles Interesse
    2. KERZE (0-20)       — Close Position + Wick Bonus − Wick Penalty
    3. TIMING (0-20)      — Extension: Sweet Spot, Early Entry, oder Chase?
    4. LIQUIDITÄT (0-15)  — Dollar Volume: Tradeable?
    5. MOMENTUM (0-15)    — Richtige Richtung + Stärke (OHNE RVOL, Extension-Penalty)
    6. KONTEXT (0-10)     — Vortag: Konsolidierung = bester Base
    ─────────────────────────────────────────────────────
    = Max 100 Punkte

    FIXES v2:
    - Wick Penalty: Grosse gegenläufige Wick bestraft (Hammer/Shooting Star)
    - Timing: Early Entry (0.2-0.5x ATR) bekommt Punkte statt 0
    - Timing Fallback: <0.5% Change = 0 Punkte (nicht mehr 8)
    - Momentum: RVOL rausgenommen (bereits in Kat 1), Extension-Penalty eingebaut
    """
    score = 0
    is_long = direction == "long"
    abs_change = abs(change_pct) if change_pct else 0

    # ── 1. VOLUME (0-20) ──
    # RVOL = einziger Volume-Indikator, wird NUR HIER bewertet
    if rvol is not None and rvol > 0:
        if rvol >= 3.0:
            score += 20   # Institutionell
        elif rvol >= 2.0:
            score += 16
        elif rvol >= 1.5:
            score += 12
        elif rvol >= 1.0:
            score += 6

    # ── 2. KERZE (0-20) ──
    # Close Position (0-14): Wo hat die Kerze geschlossen?
    # Wick Bonus (0-6): Wenig Rejection-Wick = Käufer/Verkäufer halten Preis
    # Wick Penalty (0 bis -6): Grosse gegenläufige Wick = versteckter Gegendruc
    candle_score = 0
    if close_pos is not None:
        if is_long:
            # Close near High = Käufer dominieren
            if close_pos >= 0.80:
                candle_score += 14
            elif close_pos >= 0.65:
                candle_score += 10
            elif close_pos >= 0.50:
                candle_score += 5

            # Bonus: Wenig Upper Wick = kein Rejection von oben
            if upper_wick_pct is not None:
                if upper_wick_pct < 15:
                    candle_score += 6
                elif upper_wick_pct < 25:
                    candle_score += 3

            # NEU: Penalty — Grosse Lower Wick bei Long = Verkaufsdruck war da
            # Auch wenn Close near High: Hammer-Kerzen sind trügerisch
            if lower_wick_pct is not None:
                if lower_wick_pct > 50:
                    candle_score -= 6   # Hammer — massiver Sell-Off, Recovery fragwürdig
                elif lower_wick_pct > 30:
                    candle_score -= 3   # Deutlicher Verkaufsdruck trotz Close near High
        else:
            # Short: Close near Low = Verkäufer dominieren
            if close_pos <= 0.20:
                candle_score += 14
            elif close_pos <= 0.35:
                candle_score += 10
            elif close_pos <= 0.50:
                candle_score += 5

            # Bonus: Wenig Lower Wick = kein Bounce von unten
            if lower_wick_pct is not None:
                if lower_wick_pct < 15:
                    candle_score += 6
                elif lower_wick_pct < 25:
                    candle_score += 3

            # NEU: Penalty — Grosse Upper Wick bei Short = Kaufdruck war da
            if upper_wick_pct is not None:
                if upper_wick_pct > 50:
                    candle_score -= 6   # Shooting Star — Käufer wehren sich massiv
                elif upper_wick_pct > 30:
                    candle_score -= 3   # Deutlicher Kaufdruck trotz Close near Low

    score += max(0, candle_score)  # Kerze-Kategorie nie negativ

    # ── 3. TIMING (0-20) ──
    # Misst ob man early, im Sweet Spot, oder zu spät dran ist
    # V2: High-ATR Penalty — bei >8% ATR ist <0.5x extension oft Rauschen, nicht "early entry"
    if atr_pct and atr_pct > 0:
        extension = abs_change / atr_pct
        if abs_change < 0.5:
            pass  # Zu wenig Bewegung — kein Timing-Score (Rauschen)
        elif 0.5 <= extension <= 2.0 and abs_change <= 6:
            score += 20   # Sweet Spot — genug Bewegung, nicht extended
        elif extension < 0.5:
            # "Early Entry" — aber bei hoher ATR oft nur Rauschen
            if atr_pct >= 8.0:
                # High-ATR Stock (wie NAT 10.8%): 0.4x ATR = normales Rauschen
                score += 4    # Minimal — Move ist nicht signifikant
            elif atr_pct >= 5.0:
                score += 8    # Moderate ATR: vielleicht early, vielleicht Noise
            else:
                score += 12   # Low-ATR: echtes Early Entry
        elif extension <= 3.0 and abs_change <= 8:
            score += 14   # Leicht extended aber noch tradeable
        elif extension <= 3.5 and abs_change <= 10:
            score += 7    # Getting late — nur noch mit starkem Catalyst
        # else: 0 — Chase territory (>3.5x ATR oder >10%)
    else:
        # Fallback ohne ATR (z.B. Krypto) — nur auf abs_change basiert
        # V67.5: Krypto hat hoehere typische Moves → Schwellen angepasst
        # BTC typisch 1-3% daily, Altcoins 3-8% daily
        if 2.0 <= abs_change <= 8.0:
            score += 15   # Sweet Spot fuer Krypto
        elif 8.0 < abs_change <= 15.0:
            score += 10   # Etwas heiss, aber bei Krypto normal
        elif 1.0 <= abs_change < 2.0:
            score += 7    # Moderate Bewegung
        elif 15.0 < abs_change <= 25.0:
            score += 4    # Chase Territory, aber bei Krypto moeglich
        elif 0.5 <= abs_change < 1.0:
            score += 3    # Kaum Bewegung
        # else: 0 — Entweder nichts (<0.5%) oder extrem (>25%)

    # ── 4. LIQUIDITÄT (0-15) ──
    if dollar_volume:
        if dollar_volume >= 5_000_000:
            score += 15
        elif dollar_volume >= 1_000_000:
            score += 12
        elif dollar_volume >= 500_000:
            score += 8
        elif dollar_volume >= 100_000:
            score += 3

    # ── 5. MOMENTUM QUALITÄT (0-15) ──
    # V2: ATR-NORMALISIERT — +4% bei 2% ATR = stark, +4% bei 10% ATR = Rauschen
    # move_atr_ratio = wie signifikant ist der Move relativ zur normalen Volatilität?
    momentum_pts = 0
    if change_pct is not None:
        # ATR-normalisiertes Momentum (primäre Bewertung)
        # V69.1 AUDIT FIX: Fallback 1.0→0.5 bei fehlendem ATR.
        # Ohne ATR-Daten kann man die Signifikanz nicht beurteilen →
        # konservativer Default (0.5 = "moderater Move") statt 1.0 ("solider Move").
        # Behebt 2 fehlende Tests in test_setup_score.py.
        move_atr_ratio = abs_change / atr_pct if (atr_pct and atr_pct > 0) else 0.5

        # Ist die Richtung korrekt?
        direction_ok = (is_long and change_pct > 0) or (not is_long and change_pct < 0)

        if direction_ok:
            # ATR-basierte Schwellen (funktioniert für $5 und $500 Aktien)
            if move_atr_ratio >= 1.5:
                momentum_pts = 15   # Signifikanter Move (>1.5x ATR) = echtes Momentum
            elif move_atr_ratio >= 1.0:
                momentum_pts = 13   # Solider Move (1x ATR) = überzeugend
            elif move_atr_ratio >= 0.7:
                momentum_pts = 10   # Guter Move (0.7x ATR) = bestätigt
            elif move_atr_ratio >= 0.5:
                momentum_pts = 7    # Moderater Move = Ansatz da
            elif move_atr_ratio >= 0.3:
                momentum_pts = 4    # Schwacher Move = kaum über Rauschen
            else:
                momentum_pts = 1    # Minimal = nur Richtung stimmt

            # Absolute Mindest-Schwelle: <0.5% Change ist immer Rauschen
            if abs_change < 0.5:
                momentum_pts = 0
        # Falsche Richtung: 0 Punkte

        # Extension-Penalty auf Momentum (Chase-Schutz)
        # MEDIUM-2 FIX (Audit V1): momentum_pts ist immer >= 0, min(x, 0) == 0 ist aequivalent.
        # Vereinfacht fuer Lesbarkeit. Semantik unveraendert.
        if abs_change >= 20:
            momentum_pts = 0
        elif abs_change >= 15:
            momentum_pts = min(momentum_pts, 3)
        elif abs_change > 10:
            momentum_pts = min(momentum_pts, 8)

    score += momentum_pts

    # ── 6. KONTEXT / VORTAG (0-10) ──
    # Wenig Bewegung gestern = Konsolidierung = Base für Breakout
    # Viel Bewegung gestern = Continuation oder Exhaustion = unsicherer
    if vortag_pct is not None:
        abs_vortag = abs(vortag_pct)
        if abs_vortag < 1.5:
            score += 10   # Konsolidierung → Breakout
        elif abs_vortag < 3.0:
            score += 6
        elif abs_vortag < 5.0:
            score += 3

    return min(100, max(0, score))


def calculate_setup_score_crypto(change_pct, rvol, close_pos, upper_wick_pct, lower_wick_pct,
                                  vortag_pct, vol_24h, price, market_cap, direction="long",
                                  high_24h=None, low_24h=None):
    """Setup Quality Score 0-100 für KRYPTO. Aktien-Score bleibt unverändert."""
    score = 0
    is_long = direction == "long"
    abs_change = abs(change_pct) if change_pct else 0
    est_atr = estimate_crypto_atr(market_cap, high_24h, low_24h, price)
    # 1. VOLUME (0-20)
    if rvol is not None and rvol > 0:
        if rvol >= 3.0:   score += 20
        elif rvol >= 2.0: score += 16
        elif rvol >= 1.5: score += 12
        elif rvol >= 1.0: score += 6
        elif rvol >= 0.7: score += 3
    # 2. KERZE (0-20)
    c = 0
    if close_pos is not None:
        if is_long:
            if close_pos >= 0.80: c += 14
            elif close_pos >= 0.65: c += 10
            elif close_pos >= 0.50: c += 5
            if upper_wick_pct is not None:
                if upper_wick_pct < 15: c += 6
                elif upper_wick_pct < 25: c += 3
            if lower_wick_pct is not None:
                if lower_wick_pct > 50: c -= 6
                elif lower_wick_pct > 30: c -= 3
        else:
            if close_pos <= 0.20: c += 14
            elif close_pos <= 0.35: c += 10
            elif close_pos <= 0.50: c += 5
            if lower_wick_pct is not None:
                if lower_wick_pct < 15: c += 6
                elif lower_wick_pct < 25: c += 3
            if upper_wick_pct is not None:
                if upper_wick_pct > 50: c -= 6
                elif upper_wick_pct > 30: c -= 3
    score += max(0, c)
    # 3. TIMING (0-20) — Extension vs geschätzte ATR
    ext = abs_change / est_atr if est_atr > 0 else 1.0
    if abs_change < 0.3:      pass              # Zu klein → 0
    elif ext < 0.3:           score += 2         # Kaum Bewegung vs ATR
    elif 0.3 <= ext < 0.5:   score += 8          # Unterdurchschnittlich
    elif 0.5 <= ext <= 2.0:  score += 20         # Ideal: 0.5-2x ATR
    elif 2.0 < ext <= 3.0:   score += 14         # Leicht überdehnt
    elif 3.0 < ext <= 4.0:   score += 7          # Stark überdehnt
    # >4x ATR → 0 Punkte (zu riskant)
    # 4. LIQUIDITÄT (0-15)
    if vol_24h:
        if vol_24h >= 100_000_000:   score += 15
        elif vol_24h >= 20_000_000:  score += 13
        elif vol_24h >= 5_000_000:   score += 10
        elif vol_24h >= 1_000_000:   score += 6
        elif vol_24h >= 100_000:     score += 2
    # 5. MOMENTUM (0-15)
    m = 0
    if change_pct is not None:
        mar = abs_change / est_atr if est_atr > 0 else 0
        d_ok = (is_long and change_pct > 0) or (not is_long and change_pct < 0)
        if d_ok:
            if mar >= 1.5:   m = 15
            elif mar >= 1.0: m = 12
            elif mar >= 0.7: m = 9
            elif mar >= 0.5: m = 6
            elif mar >= 0.3: m = 3
            else:            m = 1
            if abs_change < 0.3: m = 0
        if abs_change >= 40:   m = min(m, 0)
        elif abs_change >= 25: m = min(m, 3)
        elif abs_change > 15:  m = min(m, 8)
    score += m
    # 6. KONTEXT (0-10) — 6d-avg
    if vortag_pct is not None:
        av = abs(vortag_pct)
        if av < 0.8:   score += 10
        elif av < 1.5: score += 7
        elif av < 3.0: score += 4
        elif av < 5.0: score += 2
    return min(100, max(0, score))


def calculate_alpha_score_crypto(rvol, vortag_pct, change_pct, market_cap,
                                  high_24h=None, low_24h=None, price=None):
    """Alpha Score 0-100 für KRYPTO — Market-Cap-aware.

    HINWEIS: Alpha und Setup bewerten Vortag BEWUSST gegensätzlich:
    - Alpha = "Wie interessant ist diese Coin?" → hoher Vortag = mehr Bewegung = interessanter
    - Setup = "Wie gut ist der Einstieg JETZT?" → niedriger Vortag = Konsolidierung = besserer Einstieg
    Das ist kein Widerspruch: Eine Coin kann hochinteressant sein (Alpha 80+) aber gerade
    keinen guten Einstieg bieten (Setup 40), weil sie gestern schon stark gelaufen ist.
    """
    est_atr = estimate_crypto_atr(market_cap, high_24h, low_24h, price)
    est_atr = max(est_atr, 0.001)  # Minimum threshold to prevent division issues
    rvol_safe = min(max(rvol or 0, 0), 8)
    rvol_score = (math.log(1 + rvol_safe) / math.log(9)) * 35
    atr_ratio = min(abs(change_pct or 0) / est_atr, 3.0) if est_atr > 0 else 0
    change_score = (math.sqrt(atr_ratio) / math.sqrt(3.0)) * 35
    vortag_score = (min(abs(vortag_pct or 0), 5.0) / 5.0) * 30
    return round(rvol_score + vortag_score + change_score, 0)


def is_signal_significant(change_pct, atr_pct, multiplier=1.5):
    """
    Prüft ob eine Preisbewegung signifikant ist im Kontext der Volatilität.

    Gemini's Kritik: "5% Move in niedrigem VIX = signifikant, 5% in Crash = Rauschen"

    Lösung: Change muss > ATR * multiplier sein

    Beispiel:
    - ATR = 2% (ruhiger Markt): 5% Move ist 2.5x ATR = SIGNIFIKANT
    - ATR = 8% (volatiler Markt): 5% Move ist 0.6x ATR = RAUSCHEN
    """
    if atr_pct <= 0:
        return True  # Fallback wenn keine ATR

    significance_ratio = abs(change_pct) / atr_pct
    return significance_ratio >= multiplier


def calculate_exhaustion_score(change_24h, change_7d, btc_change_7d, rvol, close_pos,
                                upper_wick_pct, lower_wick_pct, market_cap,
                                high_24h=None, low_24h=None, price=None,
                                vol_24h=None, prev_vol_24h=None, change_1h=None,
                                change_14d=None, change_30d=None,
                                btc_change_14d=None, btc_change_30d=None,
                                funding_rate=None, oi_volume_ratio=None,
                                coin_changes_14d=None, btc_changes_14d=None):
    """
    Exhaustion Score 0-100 — Wie wahrscheinlich ist eine Korrektur?

    Misst 8 Dimensionen der Überdehnung (Multi-Timeframe + Derivatives):
    1. ATR-EXTENSION (0-20)       — Wie weit über normalem ATR? (>3x = extrem)
    2. RSI-PROXY (0-15)           — Überkauft? (simuliert via 7d-Momentum)
    3. VOL-DIVERGENZ (0-15)       — Preis steigt aber Volume sinkt? (Distribution)
    4. WICK-REJECTION (0-13)      — Grosse Upper Wicks = Verkaufsdruck von oben
    5. BTC-DIVERGENZ (0-12)       — Je schwächer BTC, desto fragiler der Altcoin-Pump
    6. MICRO-TIMING (0-10)        — 1h-Daten: Dreht der Coin gerade? (Echtzeit-Signal)
    7. MULTI-TF PERSISTENZ (0-15) — Divergenz auf 14d/30d = nachhaltig & zuverlässiger
    8. FUNDING + OI (0-10)        — Hohe FR + hohes OI = überhebelt → Liquidation risk

    Returns: (score, details_list)
    """
    score = 0
    details = []
    est_atr = estimate_crypto_atr(market_cap, high_24h, low_24h, price)

    # ── 1. ATR-EXTENSION (0-20) ──
    # Wie weit ist der 7d-Move relativ zur täglichen ATR?
    # 7d ATR-Extension: change_7d / (est_atr * sqrt(7)) für zeitnormalisierte Messung
    if est_atr > 0 and change_7d:
        # Zeitnormalisiert: 7d-Move vs erwartete 7d-Range (ATR * sqrt(7))
        expected_7d_range = est_atr * math.sqrt(7)
        extension_ratio = abs(change_7d) / expected_7d_range if expected_7d_range > 0 else 0

        if extension_ratio >= 3.0:
            score += 20
            details.append(f" Extrem überdehnt: {extension_ratio:.1f}x 7d-ATR ({change_7d:+.1f}% vs ±{expected_7d_range:.1f}%)")
        elif extension_ratio >= 2.0:
            score += 16
            details.append(f" Stark überdehnt: {extension_ratio:.1f}x 7d-ATR")
        elif extension_ratio >= 1.5:
            score += 11
            details.append(f" Überdehnt: {extension_ratio:.1f}x 7d-ATR")
        elif extension_ratio >= 1.0:
            score += 6
            details.append(f" Leicht überdehnt: {extension_ratio:.1f}x 7d-ATR")
        else:
            details.append(f" Nicht überdehnt: {extension_ratio:.1f}x 7d-ATR")
    else:
        details.append(" ATR-Extension: Keine Daten")

    # ── 2. RSI-PROXY (0-15) ──
    # Ohne echte OHLCV-History nutzen wir 24h/7d-Momentum als RSI-Proxy:
    # Wenn 24h UND 7d stark positiv → überkauft (RSI > 70)
    # Wenn 24h verlangsamt vs 7d → Momentum lässt nach (Divergenz = stärkstes Signal)
    if change_24h is not None and change_7d:
        avg_daily_7d = change_7d / 7  # Durchschnittliche tägliche Änderung
        if avg_daily_7d > 0:
            if change_7d > 20 and change_24h > 5:
                score += 15
                details.append(f" Stark überkauft: 7d={change_7d:+.1f}%, 24h={change_24h:+.1f}%")
            elif change_7d > 10 and change_24h < avg_daily_7d * 0.5:
                # Momentum-Divergenz: 7d war stark aber 24h verliert Kraft
                # Das ist oft das BESTE Signal — Smart Money steigt aus
                score += 15
                details.append(f" Momentum-Divergenz: 7d stark aber 24h verlangsamt ({change_24h:+.1f}% vs avg {avg_daily_7d:+.1f}%/d)")
            elif change_7d > 15 and change_24h > 2:
                score += 12
                details.append(f" Überkauft: 7d={change_7d:+.1f}%, 24h noch stark")
            elif change_7d > 8:
                score += 7
                details.append(f" Erhöht: 7d={change_7d:+.1f}%")
            else:
                details.append(f" Nicht überkauft: 7d={change_7d:+.1f}%")
        else:
            details.append(f" 7d-Trend nicht positiv: {change_7d:+.1f}%")
    else:
        details.append(" RSI-Proxy: Keine Daten")

    # ── 3. TURNOVER-INTENSITÄT (0-15) ──
    # Ohne historische Volumenreihe ist Volumen/MarketCap kein RVOL. Es misst nur,
    # wie intensiv der Coin relativ zu seiner Größe umgesetzt wird.
    if vol_24h and market_cap and market_cap > 0:
        turnover = (vol_24h / market_cap) * 100
        # Baseline je nach Market Cap
        mc = market_cap
        if mc > 100_000_000_000:   baseline = 3.0
        elif mc > 10_000_000_000:  baseline = 6.0
        elif mc > 1_000_000_000:   baseline = 10.0
        elif mc > 100_000_000:     baseline = 20.0
        else:                       baseline = 30.0
        turnover_intensity = turnover / baseline

        if change_7d and change_7d > 10 and turnover_intensity < 0.8:
            score += 15
            details.append(f" Turnover-Divergenz: Preis +{change_7d:.0f}% aber Intensität nur {turnover_intensity:.1f}x")
        elif change_7d and change_7d > 5 and turnover_intensity < 1.0:
            score += 9
            details.append(f" Leichte Turnover-Divergenz: {turnover_intensity:.1f}x bei +{change_7d:.0f}%")
        elif turnover_intensity >= 2.0 and change_7d and change_7d > 10:
            score += 5
            details.append(f" Hohe Turnover-Intensität: {turnover_intensity:.1f}x — möglicher Klimax")
        else:
            details.append(f" Turnover-Intensität neutral: {turnover_intensity:.1f}x")
    else:
        details.append(" Turnover-Intensität: Keine Daten")

    # ── 4. WICK-REJECTION (0-13) ──
    # Grosse Upper Wicks = Seller drücken den Preis von oben → Exhaustion-Zeichen
    if upper_wick_pct is not None and close_pos is not None:
        if upper_wick_pct > 40 and close_pos < 0.50:
            score += 13
            details.append(f" Starke Rejection: UW {upper_wick_pct:.0f}%, Close bei {close_pos:.0%} (Shooting Star)")
        elif upper_wick_pct > 30:
            score += 9
            details.append(f" Rejection-Wick: UW {upper_wick_pct:.0f}%")
        elif upper_wick_pct > 20:
            score += 5
            details.append(f" Leichte Rejection: UW {upper_wick_pct:.0f}%")
        else:
            details.append(f" Keine Rejection: UW {upper_wick_pct:.0f}%")
    else:
        details.append(" Wick-Rejection: Keine Daten")

    # ── 5. BTC-DIVERGENZ (0-12) + FIX 5: PEARSON CORRELATION ──
    # Je schwächer BTC, desto fragiler der Altcoin-Pump
    # WICHTIG: Punkte NUR wenn BTC tatsächlich schwach ist!
    # Coin +15% vs BTC +8% = keine Exhaustion-Divergenz (BTC auch stark)
    # FIX 5: Measure actual correlation using Pearson coefficient
    if btc_change_7d is not None and change_7d:
        divergence = change_7d - btc_change_7d  # Positiv = Altcoin outperformt BTC

        # S-5 AUDIT FIX: Pearson-Korrelation NUR berechnen, wenn beide 14d-Listen
        # tatsaechlich vorhanden und lang genug sind (>= 5 Punkte, Minimum von
        # _pearson_corr). Frueher fiel corr_14d ohne Daten auf 0.0 zurueck und
        # erzeugte einen Phantom "+15 Real Decoupling"-Bonus samt falschem Text.
        corr_14d = None
        if (
            coin_changes_14d
            and btc_changes_14d
            and len(coin_changes_14d) >= 5
            and len(btc_changes_14d) >= 5
        ):
            corr_14d = _pearson_corr(coin_changes_14d, btc_changes_14d)

        corr_bonus = 0
        if corr_14d is None:
            # Keine 14d-Daten -> kein Decoupling-Bonus, kein Detailtext (S-5)
            pass
        elif corr_14d < 0.3:
            # Real decoupling — altcoin moving independently from BTC
            corr_bonus = 15
            details.append(f" [FIX5] Real Decoupling: Correlation {corr_14d:.2f} — unabhängiger Pump!")
        elif corr_14d > 0.7:
            # High correlation — normal correlated move, reduce exhaustion score
            corr_bonus = -10
            details.append(f" [FIX5] Normal Correlation: {corr_14d:.2f} — nicht abnormal")
        else:
            details.append(f" [FIX5] Moderate Correlation: {corr_14d:.2f}")

        div_points = 0
        if divergence > 25 and btc_change_7d < -5:
            div_points = 12
            details.append(f" Extreme Divergenz 7d: Coin +{change_7d:.0f}% vs BTC {btc_change_7d:+.0f}% (Δ{divergence:+.0f}%)")
        elif divergence > 15 and btc_change_7d < 0:
            div_points = 9
            details.append(f" Starke Divergenz 7d: Δ{divergence:+.0f}% vs BTC {btc_change_7d:+.0f}%")
        elif divergence > 10 and btc_change_7d < 2:
            div_points = 6
            details.append(f" Moderate Divergenz 7d: Δ{divergence:+.0f}% vs BTC {btc_change_7d:+.0f}%")
        elif divergence > 5 and btc_change_7d < 3:
            # Nur Punkte wenn BTC wenigstens seitwärts/schwach ist (<+3%)
            div_points = 3
            details.append(f" Leichte Divergenz 7d: Δ{divergence:+.0f}% (BTC {btc_change_7d:+.0f}%)")
        elif divergence > 5:
            # BTC auch stark → keine echte Divergenz
            details.append(f" Keine echte Divergenz: BTC auch stark ({btc_change_7d:+.0f}%), Δ nur {divergence:+.0f}%")
        else:
            details.append(f" Keine relevante Divergenz 7d: Δ{divergence:+.0f}%")

        # M-ExhCap AUDIT FIX: Dimension 5 (Divergenz + Decoupling ZUSAMMEN) ist
        # mit 0-12 dokumentiert -> auf 12 deckeln (frueher bis zu 12+15=27).
        score += max(-10, min(12, div_points + corr_bonus))
    else:
        details.append(" BTC-Divergenz: Keine Daten")

    # ── 6. MICRO-TIMING (0-10) — 1h-Echtzeit-Signal + FIX 6: Z-Score ──
    # Die 1h-Kerze zeigt, ob der Coin GERADE kippt
    # Bester Short-Trigger: 7d stark positiv ABER letzte 1h dreht negativ
    # FIX 6: Only trigger signals when statistically significant (z-score >= 2.0)
    if change_1h is not None and change_7d:
        avg_hourly_expected = change_7d / (7 * 24)  # Erwartete stündliche Änderung

        # FIX 6: Calculate z-score for 1h change (use typical hourly changes as reference)
        # Assume typical hourly volatility range: -2% to +2% for most coins
        typical_hourly_changes = [-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2]
        z_score_1h = _z_score(change_1h, typical_hourly_changes) if change_1h is not None else 0
        downside_z = max(0.0, -z_score_1h)

        if change_7d > 8 and change_1h < -2.0 and downside_z >= 1.5:
            score += 10
            details.append(f" [FIX6] 1h-Reversal (Z={z_score_1h:.1f}): {change_1h:+.1f}% letzte Stunde! (7d war +{change_7d:.0f}%) — Kipp-Signal")
        elif change_7d > 8 and change_1h < -0.5 and downside_z >= 0.35:
            score += 7
            details.append(f" [FIX6] 1h dreht (Z={z_score_1h:.1f}): {change_1h:+.1f}% (erste Schwäche nach 7d +{change_7d:.0f}%)")
        elif change_7d > 8 and change_1h < avg_hourly_expected * 0.3 and change_1h <= 0:
            score += 4
            details.append(f" [FIX6] 1h verlangsamt (Z={z_score_1h:.1f}): {change_1h:+.2f}% (erwartet: {avg_hourly_expected:+.2f}%/h)")
        elif change_7d > 8 and change_1h > 3.0 and z_score_1h >= 2.0:
            score += 5
            details.append(f" [FIX6] 1h Blow-Off (Z={z_score_1h:.1f}): {change_1h:+.1f}% Spike! Möglicher Klimax → Reversal folgt oft")
        else:
            details.append(f" 1h neutral: {change_1h:+.2f}% (Z-score: {z_score_1h:.2f})")
    else:
        details.append(" Micro-Timing: Keine 1h-Daten")

    # ── 7. MULTI-TIMEFRAME PERSISTENZ (0-15) — FIX 7: Momentum Fading Logic ──
    # Divergenz die auf 14d und 30d bestätigt wird ist VIEL zuverlässiger
    # Ein Coin der 30d lang gegen schwachen BTC pumpt hat eine höhere Reversion-Wahrscheinlichkeit
    # FIX 7: If 7d divergence is WEAKER than 14d, momentum is fading = EXHAUSTION!
    mtf_score = 0
    mtf_details = []

    # Calculate divergences
    div_7d = (change_7d - btc_change_7d) if (change_7d and btc_change_7d is not None) else None
    div_14d = None
    div_30d = None

    if change_14d and btc_change_14d is not None:
        div_14d = change_14d - btc_change_14d
        if div_14d > 30 and btc_change_14d < 0:
            mtf_score += 8
            mtf_details.append(f"14d: Δ{div_14d:+.0f}% vs BTC {btc_change_14d:+.0f}%")
        elif div_14d > 15 and btc_change_14d < 5:
            mtf_score += 5
            mtf_details.append(f"14d: Δ{div_14d:+.0f}%")
        elif div_14d > 10:
            mtf_score += 3

    if change_30d and btc_change_30d is not None:
        div_30d = change_30d - btc_change_30d
        if div_30d > 40 and btc_change_30d < 0:
            mtf_score += 7
            mtf_details.append(f"30d: Δ{div_30d:+.0f}% vs BTC {btc_change_30d:+.0f}%")
        elif div_30d > 25 and btc_change_30d < 5:
            mtf_score += 5
            mtf_details.append(f"30d: Δ{div_30d:+.0f}%")
        elif div_30d > 15:
            mtf_score += 3

    # FIX 7: Momentum Fading Detection — if 7d divergence is weaker than 14d
    if div_7d is not None and div_14d is not None and div_7d < div_14d and div_14d > 10:
        # Momentum is fading! Add to exhaustion score
        fading_points = min(8, (div_14d - div_7d) / 5)  # Award based on how much it faded
        mtf_score += int(fading_points)
        mtf_details.append(f" [FIX7] MOMENTUM FADING: 7d Δ{div_7d:.0f}% << 14d Δ{div_14d:.0f}% — Exhaustion!")

    mtf_score = min(15, mtf_score)
    if mtf_score >= 10:
        score += mtf_score
        details.append(f" Multi-TF bestätigt: {' · '.join(mtf_details)} — nachhaltige Divergenz!")
    elif mtf_score >= 5:
        score += mtf_score
        details.append(f" Multi-TF Signal: {' · '.join(mtf_details)}")
    elif mtf_score > 0:
        score += mtf_score
        details.append(f" Teilweise Multi-TF: {' · '.join(mtf_details) if mtf_details else 'schwaches Signal'}")
    else:
        # Keine 14d/30d Divergenz — nur kurzfristiger Pump → weniger zuverlässig
        if change_14d and change_30d:
            details.append(f" Nur kurzfristige Divergenz (14d: {change_14d:+.0f}%, 30d: {change_30d:+.0f}%)")
        else:
            details.append(" Multi-TF: Keine 14d/30d-Daten")

    # ── 8. FUNDING + OPEN INTEREST (0-10) — NEU ──
    # Hohe positive Funding Rate = Markt überhebelt long → Shorts werden bezahlt
    # Hohes OI bei überkauftem Coin = viel Leverage im Markt → Liquidation cascade risk
    # funding_rate: aktueller 8h-Funding-Satz (z.B. 0.001 = 0.1%)
    # oi_ratio: holdVol / volume24 — Wie viel OI vs Tagesvolumen (>1.0 = viel Leverage)
    if funding_rate is not None:
        fr_pct = funding_rate * 100  # In Prozent umrechnen
        fr_score = 0
        fr_details = []

        # Funding Rate Scoring
        if fr_pct >= 0.1:
            # Extreme positive FR: Longs zahlen massiv → überhebelt
            fr_score += 6
            fr_details.append(f"FR {fr_pct:+.4f}% (Longs zahlen)")
        elif fr_pct >= 0.03:
            # Deutlich positive FR
            fr_score += 4
            fr_details.append(f"FR {fr_pct:+.4f}%")
        elif fr_pct >= 0.01:
            fr_score += 2
            fr_details.append(f"FR {fr_pct:+.4f}% (leicht long-lastig)")
        elif fr_pct <= -0.03:
            # Fix #5: Negative FR: Shorts zahlen → Markt bereits short-heavy → VORSICHT
            # Schwelle von -0.05% auf -0.03% gesenkt und Malus erhöht
            if fr_pct <= -0.1:
                fr_score -= 5  # Stark negativ = extrem crowded short → GEFAHR
                fr_details.append(f" FR {fr_pct:+.4f}% CROWDED SHORT — Squeeze-Gefahr!")
            else:
                fr_score -= 3  # Negativ = crowded
                fr_details.append(f"FR {fr_pct:+.4f}% (Shorts zahlen — crowded!)")

        # Open Interest Ratio (wenn verfügbar)
        if oi_volume_ratio is not None and oi_volume_ratio > 0:
            if oi_volume_ratio >= 5.0:
                fr_score += 4
                fr_details.append(f"OI/Vol {oi_volume_ratio:.1f}x (extrem gehebelt)")
            elif oi_volume_ratio >= 2.0:
                fr_score += 3
                fr_details.append(f"OI/Vol {oi_volume_ratio:.1f}x (viel Leverage)")
            elif oi_volume_ratio >= 1.0:
                fr_score += 2
                fr_details.append(f"OI/Vol {oi_volume_ratio:.1f}x")

        fr_score = max(-5, min(10, fr_score))  # Clamp: -5 bis +10
        score += fr_score
        if fr_score >= 7:
            details.append(f" Überhebelt: {' · '.join(fr_details)} — Liquidation cascade risk!")
        elif fr_score >= 4:
            details.append(f" Funding bestätigt: {' · '.join(fr_details)}")
        elif fr_score >= 1:
            details.append(f" Leichtes Funding-Signal: {' · '.join(fr_details)}")
        elif fr_score < 0:
            details.append(f" Crowded Short: {' · '.join(fr_details)} — Vorsicht!")
        else:
            details.append(f" Kein Funding-Signal: FR {fr_pct:+.4f}%")
    else:
        details.append(" Funding/OI: Kein MEXC-Perp verfügbar")

    return min(100, max(0, score)), details


def get_exhaustion_grade(score):
    """Exhaustion Grade basierend auf Score."""
    if score >= 80: return "S", "", "EXTREME EXHAUSTION"
    elif score >= 65: return "A", "", "STRONG EXHAUSTION"
    elif score >= 50: return "B", "", "MODERATE EXHAUSTION"
    elif score >= 35: return "C", "", "EARLY SIGNS"
    else: return "D", "", "NO EXHAUSTION"


def calculate_pm_quality_score(pm_change, gap_pct, pm_position, rs_vs_spy, vol_ratio,
                                shares_m=0, float_cat="UNKNOWN", has_catalyst=False,
                                pm_price=0, pm_vwap=0, **kwargs):
    """
    PM Quality Score V2 (0-100) — Wie tradeable ist dieses Setup?

    V2 Verbesserungen:
    - PM MOMENTUM (Change - Gap) = tatsächliches PM-Kaufverhalten
    - VWAP-Relation = institutionelle Bestätigung
    - FADING-Widerspruch-Penalty = starker Move + schwache Position = Trap
    - DEAD VOLUME Kill = VolR < 0.2 → Score-Cap bei 25
    - Warning Flags für sofortige Problemerkennung

    Kombiniert:
    1. MOVE + PM MOMENTUM (0-25) — Stärke + tatsächliche PM-Käufe
    2. POSITION + VWAP (0-20)    — Wo in der PM Range + VWAP Bestätigung
    3. VOLUME (0-25)             — Volume Ratio als Bestätigung
    4. RELATIVE STRENGTH (0-15)  — RS vs SPY
    5. CATALYST / FLOAT (0-15)   — Katalysator + Float-Kategorie
    6. PENALTIES (-5 bis -20)    — Fading, Contradiction, Dead Volume

    Returns: (score, breakdown_dict, confidence_level)
    """
    score = 0
    breakdown = {}
    warnings = []  # Sofort sichtbare Probleme
    is_up = pm_change > 0
    abs_change = abs(pm_change)

    # PM Momentum = Change - Gap → wie viel ist IN der PM-Session passiert?
    # Gap +5%, Change +5% → PM Momentum = 0% (nur Gap, kein PM-Kauf)
    # Gap +2%, Change +5% → PM Momentum = +3% (aktives Kaufen im PM!)
    pm_momentum = pm_change - gap_pct  # Positiv = PM-Käufe, Negativ = PM-Verkäufe
    abs_pm_momentum = abs(pm_momentum)
    # Momentum in die richtige Richtung? (Long: positiv, Short: negativ)
    momentum_aligned = (is_up and pm_momentum > 0) or (not is_up and pm_momentum < 0)

    # ── 1. MOVE + PM MOMENTUM (0-25) ──
    move_score = 0

    # Basis: Absolute Veränderung (wie vorher)
    if abs_change >= 10:
        move_score = 16
    elif abs_change >= 7:
        move_score = 14
    elif abs_change >= 5:
        move_score = 11
    elif abs_change >= 3:
        move_score = 8
    elif abs_change >= 2:
        move_score = 5
    else:
        move_score = 2

    # PM Momentum Bonus/Penalty (max ±9)
    # Aktives Kaufen/Verkaufen im PM ist WICHTIGER als nur ein Gap
    if momentum_aligned:
        if abs_pm_momentum >= 3:
            move_score += 9   # Starkes PM Buying/Selling
        elif abs_pm_momentum >= 1.5:
            move_score += 6
        elif abs_pm_momentum >= 0.5:
            move_score += 3
        # PM Momentum < 0.5% → kein Bonus (nur Gap, kein PM-Interesse)
    else:
        # PM Momentum GEGEN die Richtung = Fade!
        if abs_pm_momentum >= 2:
            move_score -= 5   # PM verkauft den Gap ab
            warnings.append("PM fading")
        elif abs_pm_momentum >= 1:
            move_score -= 3

    move_score = max(0, min(move_score, 25))
    breakdown["move"] = round(move_score, 1)
    breakdown["pm_momentum"] = round(pm_momentum, 2)
    score += move_score

    # ── 2. POSITION + VWAP (0-20) ──
    pos_score = 0
    if is_up:
        # Long: Position oben = stark
        if pm_position >= 80:
            pos_score = 16
        elif pm_position >= 65:
            pos_score = 13
        elif pm_position >= 50:
            pos_score = 8
        elif pm_position >= 35:
            pos_score = 3
        else:
            pos_score = 0   # Fading → Null
    else:
        # Short: Position unten = stark
        if pm_position <= 20:
            pos_score = 16
        elif pm_position <= 35:
            pos_score = 13
        elif pm_position <= 50:
            pos_score = 8
        elif pm_position <= 65:
            pos_score = 3
        else:
            pos_score = 0

    # VWAP Bestätigung (max +4)
    # Preis über VWAP bei Long = institutionelle Käufer, unter VWAP bei Short = Schwäche
    if pm_price > 0 and pm_vwap > 0:
        vwap_dist_pct = ((pm_price - pm_vwap) / pm_vwap) * 100
        if is_up:
            if vwap_dist_pct >= 1.0:
                pos_score += 4   # Deutlich über VWAP → bestätigt
            elif vwap_dist_pct >= 0:
                pos_score += 2   # Knapp über VWAP → OK
            else:
                pos_score -= 2   # Unter VWAP bei Long → Warnung
                warnings.append("unter VWAP")
        else:
            if vwap_dist_pct <= -1.0:
                pos_score += 4
            elif vwap_dist_pct <= 0:
                pos_score += 2
            else:
                pos_score -= 2
                warnings.append("über VWAP")

    pos_score = max(0, min(pos_score, 20))
    breakdown["position"] = round(pos_score, 1)
    score += pos_score

    # ── 3. VOLUME QUALITÄT (0-25) — KRITISCH! ──
    vol_score = 0
    if vol_ratio >= 3.0:
        vol_score = 25  # Massives Volume
    elif vol_ratio >= 2.0:
        vol_score = 22
    elif vol_ratio >= 1.5:
        vol_score = 18
    elif vol_ratio >= 1.0:
        vol_score = 14
    elif vol_ratio >= 0.5:
        vol_score = 8
    elif vol_ratio >= 0.3:
        vol_score = 4   # Dünn
    elif vol_ratio >= 0.2:
        vol_score = 2   # Sehr dünn
        warnings.append(" dünnes Volume")
    else:
        vol_score = 0   # DEAD Volume
        warnings.append(" kaum Volume")
    breakdown["volume"] = round(vol_score, 1)
    score += vol_score

    # ── 4. RELATIVE STRENGTH (0-15) ──
    rs_score = 0
    if is_up:
        if rs_vs_spy >= 5:
            rs_score = 15
        elif rs_vs_spy >= 3:
            rs_score = 12
        elif rs_vs_spy >= 1:
            rs_score = 8
        elif rs_vs_spy >= 0:
            rs_score = 4
        else:
            rs_score = 0
    else:
        if rs_vs_spy <= -5:
            rs_score = 15
        elif rs_vs_spy <= -3:
            rs_score = 12
        elif rs_vs_spy <= -1:
            rs_score = 8
        elif rs_vs_spy <= 0:
            rs_score = 4
        else:
            rs_score = 0
    breakdown["rs"] = round(rs_score, 1)
    score += rs_score

    # ── 5. CATALYST + FLOAT (0-15) ──
    cat_score = 0
    # Katalysator-Bewertung: Bullish = Bonus, Bearish = PENALTY!
    _catalysts_list = kwargs.get("catalysts", []) if "catalysts" in kwargs else []
    _has_bearish_cat = any(c in BEARISH_CATALYSTS for c in _catalysts_list)
    _has_bullish_cat = any(c in BULLISH_CATALYSTS for c in _catalysts_list)
    if _has_bearish_cat:
        # Offering, Lawsuit, Downgrade → WARNUNG statt Bonus!
        cat_score -= 5
        _bear_cats = [c for c in _catalysts_list if c in BEARISH_CATALYSTS]
        warnings.append(f" BEARISH Katalysator: {', '.join(_bear_cats)} — Vorsicht!")
    elif has_catalyst and _has_bullish_cat:
        cat_score += 8
    elif has_catalyst:
        # Neutral catalyst (Earnings, FDA, Split) — moderate Bonus
        cat_score += 4

    if float_cat in ("NANO", "MICRO") and abs_change >= 5:
        cat_score += 7
    elif float_cat in ("NANO", "MICRO"):
        cat_score += 4
    elif float_cat in ("SMALL",):
        cat_score += 3
    elif float_cat in ("MEDIUM",):
        cat_score += 2
    elif float_cat in ("LARGE", "MEGA"):
        cat_score += 1

    cat_score = min(cat_score, 15)
    breakdown["catalyst_float"] = round(cat_score, 1)
    score += cat_score

    # ══════════════════════════════════════════════════════════
    # ── 6. PENALTIES — Widersprüche und Killsignale ──
    # ══════════════════════════════════════════════════════════
    penalty = 0

    # FADING CONTRADICTION: Starker Move + schwache Position = TRAP
    if is_up and abs_change >= 5 and pm_position < 35:
        fading_penalty = -12
        penalty += fading_penalty
        warnings.append("FADING: Starker Gap wird abverkauft!")
    elif not is_up and abs_change >= 5 and pm_position > 65:
        fading_penalty = -12
        penalty += fading_penalty
        warnings.append("BOUNCE: Gap Down wird aufgekauft!")

    # STALE GAP: Gap aber kein PM Momentum = keiner interessiert sich
    if abs_change >= 3 and abs_pm_momentum < 0.3:
        stale_penalty = -5
        penalty += stale_penalty
        warnings.append("Staler Gap: Kein PM-Interesse")

    # DEAD VOLUME KILL: VolR < 0.2 → Score gecapped bei 25
    is_dead_volume = vol_ratio < 0.2

    breakdown["penalty"] = penalty
    score += penalty

    # ── FINAL SCORE ──
    score = max(0, min(round(score), 100))

    # Dead Volume Cap — NACH allen Berechnungen
    if is_dead_volume:
        score = min(score, 25)
        warnings.insert(0, "DEAD VOLUME — Score gecapped!")

    # Confidence Level
    if score >= 75:
        confidence = "HIGH"
    elif score >= 55:
        confidence = "MEDIUM"
    elif score >= 35:
        confidence = "LOW"
    else:
        confidence = "AVOID"

    breakdown["warnings"] = warnings

    return score, breakdown, confidence
