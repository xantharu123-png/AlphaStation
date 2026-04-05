"""
Volume Profile Engine V1.1
POC, Value Area, HVN/LVN, Signal Generation

V1.1 AUDIT FIXES:
- Close-Weighted Volume Distribution (statt gleichmaessig)
- Strategy-Type-Aware Signal Scoring (bounce/breakout/default)
- HVN Smoothing (3-Bin Rolling Average vor Peak-Detection)
- HVN Clustering (merge benachbarte HVN)
- Korrekte HVN-Interpretation bei Bounce (nahes HVN = Volume-Akzeptanz)
"""

import numpy as np
from typing import Dict, List, Optional, Tuple


def fetch_historical_ohlcv(ticker, api_key, days=200, rate_limited_get_fn=None):
    """Holt historische OHLCV-Bars von Polygon. Gleicher Call wie fetch_historical_closes."""
    try:
        from datetime import datetime, timedelta
        import requests
        get_fn = rate_limited_get_fn or requests.get
        end_date = datetime.now()
        calendar_days = int(days * 1.5) + 20
        start_date = end_date - timedelta(days=calendar_days)
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        params = {"apiKey": api_key, "limit": calendar_days, "sort": "asc"}
        resp = get_fn(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
            return None
        bars = []
        for bar in data["results"]:
            bars.append({
                "open": bar.get("o", 0), "high": bar.get("h", 0),
                "low": bar.get("l", 0), "close": bar.get("c", 0),
                "volume": bar.get("v", 0), "time": bar.get("t", 0),
            })
        return bars if bars else None
    except Exception:
        return None


def calculate_volume_profile(ohlcv_data, lookback_days=200, num_bins=None, atr_value=None):
    """
    Berechnet Volume Profile aus Daily OHLCV Bars.
    
    V1.1: Close-Weighted Distribution — mehr Volume nahe Close-Preis.
    Spiegelt reales Intraday-Verhalten wider (MOC Orders, Closing Auctions).
    """
    if not ohlcv_data or len(ohlcv_data) < 20:
        return None
    
    bars = ohlcv_data[-lookback_days:] if len(ohlcv_data) > lookback_days else ohlcv_data
    valid_bars = [b for b in bars if b.get("high", 0) > b.get("low", 0) and b.get("volume", 0) > 0]
    if len(valid_bars) < 20:
        return None
    
    all_highs = [b["high"] for b in valid_bars]
    all_lows = [b["low"] for b in valid_bars]
    price_low = min(all_lows)
    price_high = max(all_highs)
    price_range = price_high - price_low
    if price_range <= 0:
        return None
    
    # Bin-Groesse
    if num_bins is not None:
        bin_size = price_range / num_bins
    elif atr_value and atr_value > 0:
        bin_size = atr_value / 10
        num_bins = max(20, int(price_range / bin_size))
    else:
        num_bins = 80
        bin_size = price_range / num_bins
    
    num_bins = max(20, min(300, num_bins))
    bin_size = price_range / num_bins
    
    # CLOSE-WEIGHTED Volume Distribution
    volume_bins = np.zeros(num_bins)
    
    for bar in valid_bars:
        bar_low = bar["low"]
        bar_high = bar["high"]
        bar_close = bar["close"]
        bar_vol = bar["volume"]
        bar_range = bar_high - bar_low
        
        if bar_range <= 0 or bar_vol <= 0:
            continue
        
        start_bin = max(0, int((bar_low - price_low) / bin_size))
        end_bin = min(num_bins - 1, int((bar_high - price_low) / bin_size))
        
        if start_bin == end_bin:
            volume_bins[start_bin] += bar_vol
        else:
            # Dreieck-Verteilung mit Peak am Close-Bin
            close_bin = min(num_bins - 1, max(0, int((bar_close - price_low) / bin_size)))
            weights = np.zeros(end_bin - start_bin + 1)
            for idx, b in enumerate(range(start_bin, end_bin + 1)):
                dist = abs(b - close_bin)
                weights[idx] = 1.0 / (1.0 + dist)
            total_weight = np.sum(weights)
            if total_weight > 0:
                weights = weights / total_weight * bar_vol
                for idx, b in enumerate(range(start_bin, end_bin + 1)):
                    volume_bins[b] += weights[idx]
    
    total_volume = np.sum(volume_bins)
    if total_volume <= 0:
        return None
    
    # POC
    poc_bin = int(np.argmax(volume_bins))
    poc_price = price_low + (poc_bin + 0.5) * bin_size
    poc_volume = float(volume_bins[poc_bin])
    
    # Value Area (70%)
    va_high, va_low = _calculate_value_area(volume_bins, poc_bin, price_low, bin_size, total_volume)
    
    # HVN (smoothed + clustered)
    hvn_list = _find_hvn_smoothed(volume_bins, poc_volume, price_low, bin_size, poc_bin)
    
    # LVN
    lvn_list = _find_lvn(volume_bins, poc_volume, price_low, bin_size)
    
    bins_output = []
    for i in range(num_bins):
        bins_output.append({
            "price": round(price_low + (i + 0.5) * bin_size, 4),
            "volume": round(float(volume_bins[i]), 0),
        })
    
    return {
        "poc": round(poc_price, 4),
        "poc_volume": round(poc_volume, 0),
        "va_high": round(va_high, 4),
        "va_low": round(va_low, 4),
        "total_volume": round(float(total_volume), 0),
        "hvn": hvn_list,
        "lvn": lvn_list,
        "bins": bins_output,
        "price_range": {"low": round(price_low, 4), "high": round(price_high, 4)},
        "bin_size": round(bin_size, 4),
        "lookback_bars": len(valid_bars),
    }


def _calculate_value_area(volume_bins, poc_bin, price_low, bin_size, total_volume, pct=0.70):
    """Value Area: Preisbereich der 70% des Volumens enthaelt. Expansion vom POC."""
    target_volume = total_volume * pct
    accumulated = float(volume_bins[poc_bin])
    va_low_bin = poc_bin
    va_high_bin = poc_bin
    n = len(volume_bins)
    
    while accumulated < target_volume:
        can_go_up = va_high_bin + 1 < n
        can_go_down = va_low_bin - 1 >= 0
        if not can_go_up and not can_go_down:
            break
        vol_up = float(volume_bins[va_high_bin + 1]) if can_go_up else -1
        vol_down = float(volume_bins[va_low_bin - 1]) if can_go_down else -1
        # V68: Overshoot-Guard + Boundary-Exhaustion-Check
        remaining = target_volume - accumulated
        if vol_up >= vol_down:
            va_high_bin += 1
            accumulated += min(vol_up, remaining)
        else:
            va_low_bin -= 1
            accumulated += min(vol_down, remaining)
    
    # VA High = obere Kante des hoechsten Bins, VA Low = untere Kante des niedrigsten Bins
    va_high_price = price_low + (va_high_bin + 1) * bin_size
    va_low_price = price_low + va_low_bin * bin_size
    return va_high_price, va_low_price


def _find_hvn_smoothed(volume_bins, poc_volume, price_low, bin_size, poc_bin,
                        threshold_pct=0.40, smooth_window=3, merge_distance=3):
    """
    HVN mit Smoothing + Clustering.
    V1.1: Rolling Average, Threshold 40%, merge benachbarter Peaks.
    """
    n = len(volume_bins)
    if n < smooth_window + 2:
        return []
    
    smoothed = np.convolve(volume_bins, np.ones(smooth_window) / smooth_window, mode='same')
    threshold = poc_volume * threshold_pct
    raw_hvn = []
    
    for i in range(1, n - 1):
        if i == poc_bin:
            continue
        vol = float(smoothed[i])
        if vol < threshold:
            continue
        if vol >= float(smoothed[i - 1]) and vol >= float(smoothed[i + 1]):
            raw_hvn.append({
                "bin": i,
                "price": round(price_low + (i + 0.5) * bin_size, 4),
                "volume": round(float(volume_bins[i]), 0),
                "pct_of_poc": round(float(volume_bins[i]) / poc_volume * 100, 1) if poc_volume > 0 else 0,
            })
    
    if not raw_hvn:
        return []
    
    # Clustering
    raw_hvn.sort(key=lambda x: x["bin"])
    clusters = []
    current_cluster = [raw_hvn[0]]
    for hvn in raw_hvn[1:]:
        if hvn["bin"] - current_cluster[-1]["bin"] <= merge_distance:
            current_cluster.append(hvn)
        else:
            clusters.append(current_cluster)
            current_cluster = [hvn]
    clusters.append(current_cluster)
    
    merged = []
    for cluster in clusters:
        best = max(cluster, key=lambda x: x["volume"])
        merged.append({"price": best["price"], "volume": best["volume"], "pct_of_poc": best["pct_of_poc"]})
    
    merged.sort(key=lambda x: x["volume"], reverse=True)
    return merged[:5]


def _find_lvn(volume_bins, poc_volume, price_low, bin_size, threshold_pct=0.15):
    """LVN: Taeler im Profil — Bins mit < 15% POC-Volume."""
    threshold = poc_volume * threshold_pct
    lvn = []
    n = len(volume_bins)
    for i in range(1, n - 1):
        vol = float(volume_bins[i])
        if vol >= threshold:
            continue
        if vol <= float(volume_bins[i - 1]) and vol <= float(volume_bins[i + 1]):
            lvn.append({"price": round(price_low + (i + 0.5) * bin_size, 4), "volume": round(vol, 0)})
    lvn.sort(key=lambda x: x["price"], reverse=True)
    return lvn[:8]


# =============================================================================
# SIGNAL-ANALYSE (Strategy-Type-Aware)
# =============================================================================

def analyze_vp_signals(vp, current_price, atr=None, direction="long", strategy_type="default"):
    """
    V1.1: STRATEGY-TYPE-AWARE Signal Scoring.
    
    strategy_type:
      "bounce"   — MA Bounce, Dip Buy: Unter/nahe POC = gut (Entry-Zone)
      "breakout" — Momentum: Ueber POC/VA = gut (Confirmation)
      "default"  — Generische Bewertung
    """
    empty = {"score_adjustment": 0, "signals": [], "summary": "N/A",
             "poc_relation": "unknown", "va_position": "unknown", "poc": 0,
             "va_high": 0, "va_low": 0,
             "near_hvn_support": False, "near_hvn_resistance": False, "in_lvn_zone": False}
    
    if not vp or not current_price or current_price <= 0:
        return empty
    
    poc = vp["poc"]
    va_high = vp["va_high"]
    va_low = vp["va_low"]
    hvn_list = vp.get("hvn", [])
    lvn_list = vp.get("lvn", [])
    proximity = atr if atr and atr > 0 else current_price * 0.015
    
    signals = []
    score_adj = 0
    is_long = direction == "long"
    is_bounce = strategy_type == "bounce"
    is_breakout = strategy_type == "breakout"
    
    # --- POC Relation ---
    poc_dist = current_price - poc
    poc_dist_pct = (poc_dist / poc * 100) if poc > 0 else 0
    
    # V2: VP STALENESS CHECK — Wenn POC >25% entfernt ist, sind die VP-Daten
    # aus einem komplett anderen Preisregime und geben keine zuverlässige Bestätigung.
    # Beispiel: NAT POC $3.43, Preis $5.25 = 53% entfernt → VP ist nutzlos
    vp_stale = abs(poc_dist_pct) > 25
    if vp_stale:
        signals.append(f"⚠️ VP-Daten veraltet — POC {poc_dist_pct:+.0f}% entfernt, anderes Preisregime")
        # Kein Score-Adjustment bei stale VP — weder Bonus noch Penalty
        # VP kann weder bestätigen noch widerlegen wenn die Daten aus $3-Range kommen
        # Return early mit 0 adjustment
        return {
            "score_adjustment": 0,
            "signals": signals,
            "poc": poc,
            "poc_relation": "stale",
            "va_position": "unknown",
            "va_low": va_low,
            "va_high": va_high,
            "near_hvn_support": False,
            "near_hvn_resistance": False,
            "in_lvn_zone": False,
            "summary": f"POC=${poc:.2f}|VA[${va_low:.2f}-${va_high:.2f}] | ⚠️ VP veraltet ({poc_dist_pct:+.0f}%)"
        }
    
    if abs(poc_dist) < proximity * 0.5:
        poc_relation = "at"
        signals.append(f"Am POC (${poc:.2f})")
        score_adj += 3 if is_bounce else 1
    elif current_price > poc:
        poc_relation = "above"
        if is_long:
            if is_bounce:
                signals.append(f"Ueber POC (${poc:.2f}, +{poc_dist_pct:.1f}%) — Bounce angelaufen")
                score_adj += 1
            elif is_breakout:
                signals.append(f"Ueber POC (${poc:.2f}, +{poc_dist_pct:.1f}%) — Breakout bestaetigt")
                score_adj += 4
            else:
                signals.append(f"Ueber POC (${poc:.2f}, +{poc_dist_pct:.1f}%)")
                score_adj += 3
        else:
            # SHORT: Über POC
            if is_bounce:
                signals.append(f"Ueber POC (${poc:.2f}, +{poc_dist_pct:.1f}%) — Short-Bounce-Entry")
                score_adj += 3
            elif is_breakout:
                signals.append(f"Ueber POC — Short gegen Volume")
                score_adj -= 3
            else:
                signals.append(f"Ueber POC — Short gegen Volume")
                score_adj -= 2
    else:
        poc_relation = "below"
        if is_long:
            if is_bounce:
                signals.append(f"Unter POC (${poc:.2f}, {poc_dist_pct:.1f}%) — Bounce-Entry-Zone")
                score_adj += 3
            elif is_breakout:
                signals.append(f"Unter POC — kein Breakout")
                score_adj -= 4
            else:
                signals.append(f"Unter POC (${poc:.2f}, {poc_dist_pct:.1f}%)")
                score_adj -= 2
        else:
            # SHORT: Unter POC
            if is_breakout:
                signals.append(f"Unter POC — Short-Breakout bestaetigt")
                score_adj += 4
            elif is_bounce:
                signals.append(f"Unter POC — Short-Bounce verpasst")
                score_adj -= 2
            else:
                signals.append(f"Unter POC — Short bestaetigt")
                score_adj += 3
    
    # --- Value Area ---
    if current_price > va_high:
        va_position = "above_va"
        if is_long:
            if is_breakout:
                signals.append(f"Ueber VA High (${va_high:.2f}) — Breakout!")
                score_adj += 3
            elif is_bounce:
                signals.append(f"Ueber VA High — Bounce abgeschlossen")
            else:
                signals.append(f"Ueber VA High (${va_high:.2f})")
                score_adj += 2
        else:
            # SHORT: Über VA High
            if is_bounce:
                signals.append(f"Ueber VA High (${va_high:.2f}) — Short-Bounce-Zone!")
                score_adj += 4
            else:
                signals.append(f"Ueber VA High — Short-Entry")
                score_adj += 2
    elif current_price < va_low:
        va_position = "below_va"
        if is_long:
            if is_bounce:
                signals.append(f"Unter VA Low (${va_low:.2f}) — Dip-Buy-Zone!")
                score_adj += 4
            elif is_breakout:
                signals.append(f"Unter VA Low — Breakout gescheitert")
                score_adj -= 3
            else:
                signals.append(f"Unter VA Low (${va_low:.2f}) — Mean-Reversion-Zone")
                score_adj += 2
        else:
            # SHORT: Unter VA Low  
            if is_breakout:
                signals.append(f"Unter VA Low — Bearish Breakout")
                score_adj += 2
            elif is_bounce:
                signals.append(f"Unter VA Low — Short-Bounce ueberdehnt")
                score_adj -= 2
            else:
                signals.append(f"Unter VA Low — Bearish")
                score_adj += 2
    else:
        va_position = "in_va"
        va_pct = (current_price - va_low) / (va_high - va_low) * 100 if (va_high - va_low) > 0 else 50
        signals.append(f"In VA ({va_pct:.0f}% | ${va_low:.2f}-${va_high:.2f})")
    
    # --- HVN Support/Resistance ---
    near_hvn_support = False
    near_hvn_resistance = False
    
    for hvn in hvn_list:
        hvn_price = hvn["price"]
        dist = current_price - hvn_price
        
        if hvn_price < current_price and abs(dist) < proximity * 1.5:
            near_hvn_support = True
            signals.append(f"HVN-Support ${hvn_price:.2f} ({hvn['pct_of_poc']:.0f}% POC)")
            score_adj += 4 if (is_long and is_bounce) else (3 if is_long else -1)
            break
        elif hvn_price > current_price and abs(dist) < proximity * 1.5:
            near_hvn_resistance = True
            if is_bounce and is_long and abs(dist) < proximity * 0.8:
                signals.append(f"HVN-Zone ${hvn_price:.2f} — Volume-Akzeptanz")
                score_adj += 2
            else:
                signals.append(f"HVN-Resistance ${hvn_price:.2f} ({hvn['pct_of_poc']:.0f}% POC)")
                score_adj += -2 if is_long else 2
            break
    
    # --- LVN Zone ---
    in_lvn_zone = False
    for lvn in lvn_list:
        if abs(current_price - lvn["price"]) < proximity * 0.5:
            in_lvn_zone = True
            if is_breakout:
                signals.append(f"LVN-Zone (${lvn['price']:.2f}) — Beschleunigung!")
                score_adj += 2
            elif is_bounce:
                signals.append(f"LVN-Zone (${lvn['price']:.2f}) — Kein Volume-Support")
                score_adj -= 2
            else:
                signals.append(f"LVN-Zone (${lvn['price']:.2f}) — Schnelle Moves")
                score_adj += 1
            break
    
    score_adj = max(-10, min(10, score_adj))
    
    # Summary
    parts = [f"POC=${poc:.2f}", f"VA[${va_low:.2f}-${va_high:.2f}]"]
    if is_bounce:
        if poc_relation == "below" and is_long: parts.append("Bounce-Entry")
        elif poc_relation == "at": parts.append("Am POC")
        elif poc_relation == "above" and is_long: parts.append("Bounce angelaufen")
    elif is_breakout:
        if poc_relation == "above" and is_long: parts.append("Breakout")
        elif poc_relation == "below" and is_long: parts.append("Kein Breakout")
    else:
        if poc_relation == "above" and is_long: parts.append("Ueber POC")
        elif poc_relation == "below" and is_long: parts.append("Unter POC")
    if near_hvn_support: parts.append("HVN-Support")
    if near_hvn_resistance and not (is_bounce and is_long): parts.append("HVN-Resist.")
    if in_lvn_zone: parts.append("LVN-Zone")
    
    return {
        "score_adjustment": score_adj, "signals": signals, "poc": poc,
        "poc_relation": poc_relation, "va_position": va_position,
        "va_high": va_high, "va_low": va_low,
        "near_hvn_support": near_hvn_support, "near_hvn_resistance": near_hvn_resistance,
        "in_lvn_zone": in_lvn_zone, "summary": " | ".join(parts),
    }


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

def get_vp_lookback_for_strategy(strategy_name):
    s = strategy_name.upper()
    if "200" in s: return 200
    elif "50" in s: return 120
    elif "21" in s or "EMA" in s: return 60
    else: return 120


def get_strategy_type_for_scanner(strategy_name):
    """Mappt Scanner-Strategienamen auf VP Strategy-Types."""
    s = strategy_name.upper()
    if any(kw in s for kw in ["BOUNCE", "DIP", "PULLBACK", "MEAN REVERSION", "FLAG", "REVERSAL"]):
        return "bounce"
    if any(kw in s for kw in ["BREAKOUT", "BREAKDOWN", "MOMENTUM", "SURGE", "GAP", "WHALE", "PENNY", "ROCKET", "GAINER", "LOSER"]):
        return "breakout"
    return "default"


def format_vp_for_display(vp, current_price, direction="long", strategy_type="default"):
    if not vp:
        return "VP: N/A"
    signals = analyze_vp_signals(vp, current_price, direction=direction, strategy_type=strategy_type)
    return f"VP: {signals['summary']}"
