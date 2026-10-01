"""
Volume Analysis Module — Extrahiert aus scanner.py (V70.4)

Volume Profile, Volume Voids, VWAP-basierte Analysen.
"""
import math


# This is intentionally not named "tick" or "market profile": the function
# only knows each bar's high, low and total volume. It allocates that volume
# uniformly by price-range overlap and therefore remains an OHLCV approximation.
OHLCV_VOLUME_PROFILE_METHOD = "proportional_bar_volume_by_price_overlap"
OHLCV_VOLUME_PROFILE_ASSUMPTION = "uniform_volume_density_within_bar_high_low"
OHLCV_VOLUME_PROFILE_SOURCE = "ohlcv_volume_profile"


def merge_lvn_bins(lvns):
    """Merge adjacent low-volume bins into continuous volume-void zones."""
    parsed = []
    for node in lvns or []:
        if not isinstance(node, dict):
            continue
        try:
            low = float(node.get("low"))
            high = float(node.get("high"))
            volume = max(0.0, float(node.get("volume", 0) or 0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(low) or not math.isfinite(high) or low <= 0 or high <= low:
            continue
        parsed.append({
            "low": low,
            "high": high,
            "volume": volume,
            "volume_pct": float(node.get("volume_pct", 0) or 0),
            "bin_count": 1,
        })
    parsed.sort(key=lambda node: (node["low"], node["high"]))

    zones = []
    for node in parsed:
        tolerance = max(abs(node["low"]) * 1e-9, math.ulp(node["low"]) * 16)
        if zones and node["low"] <= zones[-1]["high"] + tolerance:
            zone = zones[-1]
            previous_count = zone["bin_count"]
            zone["high"] = max(zone["high"], node["high"])
            zone["volume"] += node["volume"]
            zone["volume_pct"] = (
                (zone["volume_pct"] * previous_count) + node["volume_pct"]
            ) / (previous_count + 1)
            zone["bin_count"] += 1
        else:
            zones.append(dict(node))

    for zone in zones:
        zone["mid"] = zone["low"] + (zone["high"] - zone["low"]) / 2.0
    return zones


def calculate_volume_profile(ohlcv_data, num_bins=20, *, timeframe=None):
    """
    Berechnet ein approximatives Volume Profile aus historischen OHLCV-Daten.

    Das Bar-Volumen wird proportional zur High/Low-Ueberlappung auf Preis-Bins
    verteilt. Die additiven Provenienzfelder verhindern, dass dieses Ergebnis
    als tickbasiertes Volume-at-Price missverstanden wird.
    """
    if not ohlcv_data or len(ohlcv_data) < 20:  # Mind. 20 Bars für sinnvolles Profile
        return None
    
    try:
        if isinstance(num_bins, bool) or not isinstance(num_bins, int) or num_bins <= 0:
            return None

        # Only genuine positive-volume bars may determine profile geometry.
        # Preserve the legacy H/L/V-only contract without inventing missing O/C;
        # when O/C are provided, they must be finite and inside the stated range.
        contributing = []
        range_volume_only_bar_count = 0
        for raw in ohlcv_data:
            if not isinstance(raw, dict):
                continue
            if any(raw[key] is False or (isinstance(raw[key], (int, float)) and raw[key] == 0)
                   or str(raw[key]).strip().lower() in ('false', '0', 'no', 'n', 'open')
                   for key in ('is_closed', 'complete', 'completed', 'final') if key in raw):
                continue
            try:
                if any(isinstance(raw.get(key), bool) for key in ('high', 'low', 'volume')):
                    continue
                high, low, volume = (float(raw[key]) for key in ('high', 'low', 'volume'))
                if (not all(math.isfinite(value) and value > 0 for value in (high, low, volume))
                        or high < low):
                    continue
                valid_ohlc = True
                for key in ('open', 'close'):
                    if key not in raw:
                        continue
                    if isinstance(raw[key], bool):
                        valid_ohlc = False
                        break
                    value = float(raw[key])
                    if not math.isfinite(value) or not low <= value <= high:
                        valid_ohlc = False
                        break
                if not valid_ohlc:
                    continue
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            contributing.append((high, low, volume))
            range_volume_only_bar_count += int('open' not in raw or 'close' not in raw)

        contributing_bar_count = len(contributing)
        if contributing_bar_count < 20:
            return None
        input_volume = math.fsum(row[2] for row in contributing)
        if not math.isfinite(input_volume) or input_volume <= 0:
            return None
        range_high = max(row[0] for row in contributing)
        range_low = min(row[1] for row in contributing)
        
        if range_high <= range_low:
            return None
        
        # Erstelle Preis-Bins
        bin_size = (range_high - range_low) / num_bins
        if not math.isfinite(bin_size) or bin_size <= 0:
            return None
        # Reuse exact neighbouring boundaries, including the true final edge.
        # Distinct prices closer than the floating-point grid cannot support
        # native positive-width zones; do not fabricate a tick-size expansion.
        boundaries = [range_low + i * bin_size for i in range(num_bins)] + [range_high]
        if any(not math.isfinite(value) for value in boundaries) or any(
            high <= low for low, high in zip(boundaries, boundaries[1:])
        ):
            return None
        bins = []
        
        for i in range(num_bins):
            bin_low, bin_high = boundaries[i], boundaries[i + 1]
            bins.append({
                'low': bin_low,
                'high': bin_high,
                'mid': bin_low + (bin_high - bin_low) / 2,
                'volume': 0
            })
        
        # Verteile Volumen auf Bins
        # Für jeden Tag: Verteile das Tagesvolumen proportional auf die Bins die der Tag berührt
        for day_high, day_low, day_vol in contributing:
            
            day_range = day_high - day_low
            if day_range <= 0:
                # M-Doji AUDIT FIX: High==Low-Bars (Doji/1-Tick) haben keine Range.
                # Der alte 0.01-Fallback war toter Code: Die Overlap-Schleife fand
                # nie eine Ueberlappung und das Volumen ging KOMPLETT verloren.
                # Fix: Gesamtes Bar-Volumen in den Bin des Preises (Volumen-Erhaltung).
                idx = int((day_high - range_low) / bin_size) if bin_size > 0 else 0
                idx = max(0, min(num_bins - 1, idx))
                bins[idx]['volume'] += day_vol
                continue

            # Finde welche Bins dieser Tag berührt
            for bin in bins:
                # Überlappung berechnen
                overlap_low = max(bin['low'], day_low)
                overlap_high = min(bin['high'], day_high)
                
                if overlap_high > overlap_low:
                    # Proportionaler Anteil des Volumens
                    overlap_pct = (overlap_high - overlap_low) / day_range
                    bin['volume'] += day_vol * overlap_pct
        
        # Berechne Statistiken
        volumes = [b['volume'] for b in bins]
        if not volumes or any(not math.isfinite(value) or value < 0 for value in volumes) or max(volumes) == 0:
            return None
        
        total_volume = math.fsum(volumes)
        avg_volume = total_volume / num_bins
        if (not math.isfinite(total_volume) or not math.isfinite(avg_volume) or avg_volume <= 0
                or not math.isclose(total_volume, input_volume, rel_tol=1e-12)):
            return None
        max_volume = max(volumes)
        
        # Floating bin edges can perturb mathematically identical allocations.
        # Treat only 1e-12-relative ties alike: the lowest-price POC wins without
        # rounding or modifying any stored volume or real volume advantage.
        poc_idx = next(i for i in range(num_bins)
                       if math.isclose(bins[i]['volume'], max_volume, rel_tol=1e-12))
        poc_bin = bins[poc_idx]
        poc = poc_bin['mid']

        # M-VA AUDIT FIX: Value Area als KONTIGUIERLICHE POC-Expansion
        # (Vorbild: volume_profile.py _calculate_value_area). Die alte Greedy-
        # Variante sammelte die volumenstaerksten Bins unabhaengig von ihrer
        # Lage — ein Fern-Cluster blaehte die VA dann ueber leere Zonen hinweg
        # auf (z.B. VA [100,120] statt [100,111]).
        va_target = total_volume * 0.70
        accumulated = float(bins[poc_idx]['volume'])
        va_low_idx = poc_idx
        va_high_idx = poc_idx

        while accumulated < va_target:
            can_go_up = va_high_idx + 1 < num_bins
            can_go_down = va_low_idx - 1 >= 0
            if not can_go_up and not can_go_down:
                break
            vol_up = float(bins[va_high_idx + 1]['volume']) if can_go_up else -1
            vol_down = float(bins[va_low_idx - 1]['volume']) if can_go_down else -1
            # Overshoot-Guard wie im Vorbild
            remaining = va_target - accumulated
            if vol_up >= vol_down or math.isclose(vol_up, vol_down, rel_tol=1e-12):
                va_high_idx += 1
                accumulated += min(vol_up, remaining)
            else:
                va_low_idx -= 1
                accumulated += min(vol_down, remaining)

        vah = bins[va_high_idx]['high']
        val = bins[va_low_idx]['low']
        
        # Identifiziere LVNs (Low Volume Nodes) - Bins mit < 50% des Durchschnitts
        lvn_threshold = avg_volume * 0.50
        lvns = []
        
        for i, bin in enumerate(bins):
            if bin['volume'] < lvn_threshold:
                lvns.append({
                    'low': bin['low'],
                    'high': bin['high'],
                    'mid': bin['mid'],
                    'volume': bin['volume'],
                    'volume_pct': (bin['volume'] / avg_volume * 100) if avg_volume > 0 else 0
                })
        
        lvn_zones = merge_lvn_bins(lvns)

        # Identifiziere HVNs (High Volume Nodes) - Bins mit > 150% des Durchschnitts
        hvn_threshold = avg_volume * 1.50
        hvns = []
        
        for bin in bins:
            if bin['volume'] > hvn_threshold:
                hvns.append({
                    'low': bin['low'],
                    'high': bin['high'],
                    'mid': bin['mid'],
                    'volume': bin['volume'],
                    'volume_pct': (bin['volume'] / avg_volume * 100) if avg_volume > 0 else 0
                })
        
        input_quality_labels = sorted({
            str(day.get("data_quality") or "").strip()
            for day in ohlcv_data
            if isinstance(day, dict) and str(day.get("data_quality") or "").strip()
        })
        source_timeframes = sorted({
            str(day.get("source_timeframe") or "").strip()
            for day in ohlcv_data
            if isinstance(day, dict) and str(day.get("source_timeframe") or "").strip()
        })
        volume_is_estimate = any(
            day.get("volume_is_estimate") is True
            for day in ohlcv_data
            if isinstance(day, dict)
        )
        data_quality = (
            "estimated_ohlcv_bar_approximation"
            if volume_is_estimate
            else "ohlcv_bar_approximation"
        )

        return {
            'bins': bins,
            'poc': poc,
            'vah': vah,
            'val': val,
            'lvns': lvns,
            'lvn_zones': lvn_zones,
            'hvns': hvns,
            'range_high': range_high,
            'range_low': range_low,
            'avg_volume': avg_volume,
            # Additive provenance; all legacy fields above remain unchanged.
            'source': OHLCV_VOLUME_PROFILE_SOURCE,
            'approximation': True,
            'tick_data_used': False,
            'method': OHLCV_VOLUME_PROFILE_METHOD,
            'volume_allocation_assumption': OHLCV_VOLUME_PROFILE_ASSUMPTION,
            'timeframe': str(timeframe).strip() if timeframe not in (None, "") else None,
            'bin_count': num_bins,
            'bin_width': bin_size,
            'input_bar_count': len(ohlcv_data),
            'contributing_bar_count': contributing_bar_count,
            'excluded_bar_count': len(ohlcv_data) - contributing_bar_count,
            'range_volume_only_bar_count': range_volume_only_bar_count,
            'ohlc_validation': 'finite_positive_high_low_volume_and_optional_open_close_in_range',
            'volume_coverage_ratio': (
                contributing_bar_count / len(ohlcv_data) if ohlcv_data else 0.0
            ),
            'data_quality': data_quality,
            'input_data_quality': input_quality_labels,
            'source_timeframes': source_timeframes,
            'volume_is_estimate': volume_is_estimate,
        }
        
    except Exception as e:
        return None


def find_volume_voids(current_price, volume_profile, min_void_size_pct=1.0):
    """
    Findet Volume Voids (LVNs) relativ zum aktuellen Preis.
    
    Returns:
        dict mit:
        - voids_above: LVNs über aktuellem Preis (Long-Potenzial)
        - voids_below: LVNs unter aktuellem Preis (Short-Potenzial/Support fehlt)
        - nearest_void_above: Nächstes Loch über Preis
        - nearest_void_below: Nächstes Loch unter Preis
        - void_score: 0-100 Score für Trade-Potenzial
    """
    if not volume_profile or not volume_profile.get('lvns'):
        return None
    
    lvns = volume_profile.get('lvn_zones') or merge_lvn_bins(volume_profile['lvns'])
    range_size = volume_profile['range_high'] - volume_profile['range_low']
    
    if range_size <= 0:
        return None
    
    voids_above = []
    voids_below = []
    
    for lvn in lvns:
        # Void-Größe als % der Gesamtrange
        void_size_pct = (lvn['high'] - lvn['low']) / range_size * 100
        
        # Nur signifikante Voids (> min_void_size_pct)
        if void_size_pct < min_void_size_pct:
            continue
        
        lvn_with_size = {**lvn, 'size_pct': void_size_pct}
        
        if lvn['low'] > current_price:
            # Void ist ÜBER aktuellem Preis
            voids_above.append(lvn_with_size)
        elif lvn['high'] < current_price:
            # Void ist UNTER aktuellem Preis
            voids_below.append(lvn_with_size)
    
    # Sortiere nach Nähe zum aktuellen Preis
    voids_above.sort(key=lambda x: x['low'])  # Nächstes zuerst
    voids_below.sort(key=lambda x: x['high'], reverse=True)  # Nächstes zuerst
    
    # Berechne Void Score
    void_score = 0
    
    # Score für Voids über Preis (Long-Potenzial)
    if voids_above:
        nearest_above = voids_above[0]
        distance_pct = (nearest_above['low'] - current_price) / current_price * 100 if current_price > 0 else 0
        
        # Näher = besser, größer = besser
        if distance_pct < 5:  # Innerhalb 5%
            void_score += 40
        elif distance_pct < 10:
            void_score += 25
        elif distance_pct < 20:
            void_score += 10
        
        # Bonus für große Voids
        if nearest_above['size_pct'] > 5:
            void_score += 20
        elif nearest_above['size_pct'] > 3:
            void_score += 10
        
        # Bonus für mehrere Voids hintereinander
        if len(voids_above) >= 2:
            void_score += 15
    
    # Score für Voids unter Preis (fehlendes Support)
    if voids_below:
        nearest_below = voids_below[0]
        distance_pct = (current_price - nearest_below['high']) / current_price * 100 if current_price > 0 else 0
        
        # Für Short: Näher = mehr Risiko/Chance
        if distance_pct < 5:
            void_score += 15  # Kann schnell fallen
    
    return {
        'voids_above': voids_above,
        'voids_below': voids_below,
        'nearest_void_above': voids_above[0] if voids_above else None,
        'nearest_void_below': voids_below[0] if voids_below else None,
        'void_score': min(void_score, 100),
        'poc': volume_profile['poc'],
        'vah': volume_profile['vah'],
        'val': volume_profile['val']
    }


def find_volume_voids_for_chart(ohlcv_data, num_bins=20):
    """
    Findet Volume Voids für Chart-Darstellung.
    
    Returns:
        List of void zones with price_low, price_high, strength
    """
    if not ohlcv_data or len(ohlcv_data) < 10:
        return []

    try:
        if isinstance(num_bins, bool):
            return []
        num_bins = int(num_bins)
        if num_bins <= 0:
            return []

        # Keep the chart's legacy ten-bar/H-L-V contract (native profiles need
        # twenty), but use the same valid, genuine-volume geometry. Missing
        # completion metadata is left to the caller's causal bar selection;
        # an explicitly open bar never contributes to these chart zones.
        contributing = []
        for raw in ohlcv_data:
            if not isinstance(raw, dict):
                continue
            if any(raw[key] is False or (isinstance(raw[key], (int, float)) and raw[key] == 0)
                   or str(raw[key]).strip().lower() in ('false', '0', 'no', 'n', 'open')
                   for key in ('is_closed', 'complete', 'completed', 'final') if key in raw):
                continue
            try:
                if any(isinstance(raw.get(key), bool) for key in ('high', 'low', 'volume')):
                    continue
                high, low, volume = (float(raw[key]) for key in ('high', 'low', 'volume'))
                if (not all(math.isfinite(value) and value > 0 for value in (high, low, volume))
                        or high < low):
                    continue
                valid_ohlc = True
                for key in ('open', 'close'):
                    if key not in raw:
                        continue
                    if isinstance(raw[key], bool):
                        valid_ohlc = False
                        break
                    value = float(raw[key])
                    if not math.isfinite(value) or not low <= value <= high:
                        valid_ohlc = False
                        break
                if not valid_ohlc:
                    continue
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            contributing.append((high, low, volume))
        if len(contributing) < 10:
            return []

        input_volume = math.fsum(row[2] for row in contributing)
        if not math.isfinite(input_volume) or input_volume <= 0:
            return []
        range_high = max(row[0] for row in contributing)
        range_low = min(row[1] for row in contributing)
        if not math.isfinite(range_high) or not math.isfinite(range_low) or range_high <= range_low:
            return []
        bin_size = (range_high - range_low) / num_bins
        if not math.isfinite(bin_size) or bin_size <= 0:
            return []

        boundaries = [range_low + i * bin_size for i in range(num_bins)] + [range_high]
        if any(not math.isfinite(value) for value in boundaries) or any(
            high <= low for low, high in zip(boundaries, boundaries[1:])
        ):
            return []
        # Volume pro Bin
        bins = [{"low": boundaries[i],
                 "high": boundaries[i + 1],
                 "volume": 0} for i in range(num_bins)]

        for h, l, vol in contributing:
            if h == l:
                # M-Doji AUDIT FIX: High==Low-Bar — gesamtes Volumen in den Bin
                # des Preises (der 0.01-Fallback war toter Code, Volumen ging verloren).
                idx = int((h - range_low) / bin_size)
                idx = max(0, min(num_bins - 1, idx))
                bins[idx]["volume"] += vol
                continue
            day_range = h - l

            for bin in bins:
                overlap_low = max(bin["low"], l)
                overlap_high = min(bin["high"], h)
                if overlap_high > overlap_low:
                    overlap_pct = (overlap_high - overlap_low) / day_range
                    bin["volume"] += vol * overlap_pct
        
        # Verify finite allocation and volume conservation before declaring any
        # low-volume price area. Do not publish fabricated finite overflow bins.
        volumes = [bin["volume"] for bin in bins]
        if any(not math.isfinite(value) or value < 0 for value in volumes):
            return []
        total_volume = math.fsum(volumes)
        if (not math.isfinite(total_volume) or total_volume <= 0
                or not math.isclose(total_volume, input_volume, rel_tol=1e-12)):
            return []
        avg_vol = total_volume / len(bins)
        if not math.isfinite(avg_vol) or avg_vol <= 0:
            return []
        
        # Voids = Bins mit < 50% des Durchschnitts (konsistent mit Scanner)
        voids = []
        for bin in bins:
            if bin["volume"] < avg_vol * 0.5:
                strength = max(0, min(1, 1 - (bin["volume"] / avg_vol)))
                voids.append({
                    "price_low": bin["low"],
                    "price_high": bin["high"],
                    "strength": round(strength, 2)
                })
        
        return voids
    except Exception as e:
        return []


