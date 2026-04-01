"""
Volume Analysis Module — Extrahiert aus scanner.py (V70.4)

Volume Profile, Volume Voids, VWAP-basierte Analysen.
"""
import math


def calculate_volume_profile(ohlcv_data, num_bins=20):
    """
    Berechnet Volume Profile aus historischen OHLCV Daten.
    """
    if not ohlcv_data or len(ohlcv_data) < 5:
        return None
    
    try:
        # Finde Gesamt-Range
        all_highs = [d['high'] for d in ohlcv_data if d.get('high', 0) > 0]
        all_lows = [d['low'] for d in ohlcv_data if d.get('low', 0) > 0]
        
        if not all_highs or not all_lows:
            return None
        
        range_high = max(all_highs)
        range_low = min(all_lows)
        
        if range_high <= range_low:
            return None
        
        # Erstelle Preis-Bins
        bin_size = (range_high - range_low) / num_bins
        bins = []
        
        for i in range(num_bins):
            bin_low = range_low + (i * bin_size)
            bin_high = bin_low + bin_size
            bins.append({
                'low': bin_low,
                'high': bin_high,
                'mid': (bin_low + bin_high) / 2,
                'volume': 0
            })
        
        # Verteile Volumen auf Bins
        # Für jeden Tag: Verteile das Tagesvolumen proportional auf die Bins die der Tag berührt
        for day in ohlcv_data:
            day_high = day.get('high', 0)
            day_low = day.get('low', 0)
            day_vol = day.get('volume', 0)
            
            if day_high <= 0 or day_low <= 0 or day_vol <= 0:
                continue
            
            day_range = day_high - day_low
            if day_range <= 0:
                day_range = 0.01  # Minimum für Doji
            
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
        if not volumes or max(volumes) == 0:
            return None
        
        total_volume = sum(volumes)
        avg_volume = total_volume / num_bins
        max_volume = max(volumes)
        
        # Point of Control (POC) - Bin mit meistem Volumen
        poc_bin = max(bins, key=lambda x: x['volume'])
        poc = poc_bin['mid']
        
        # Value Area (70% des Volumens um POC)
        # Sortiere Bins nach Volumen absteigend
        sorted_bins = sorted(bins, key=lambda x: x['volume'], reverse=True)
        va_volume = 0
        va_target = total_volume * 0.70
        va_bins = []
        
        for bin in sorted_bins:
            va_bins.append(bin)
            va_volume += bin['volume']
            if va_volume >= va_target:
                break
        
        if va_bins:
            vah = max(b['high'] for b in va_bins)
            val = min(b['low'] for b in va_bins)
        else:
            vah = range_high
            val = range_low
        
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
        
        return {
            'bins': bins,
            'poc': poc,
            'vah': vah,
            'val': val,
            'lvns': lvns,
            'hvns': hvns,
            'range_high': range_high,
            'range_low': range_low,
            'avg_volume': avg_volume
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
    
    lvns = volume_profile['lvns']
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
        distance_pct = (nearest_above['low'] - current_price) / current_price * 100
        
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
        distance_pct = (current_price - nearest_below['high']) / current_price * 100
        
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
        # Preis-Range
        all_highs = [d["high"] for d in ohlcv_data]
        all_lows = [d["low"] for d in ohlcv_data]
        
        range_high = max(all_highs)
        range_low = min(all_lows)
        bin_size = (range_high - range_low) / num_bins
        
        # Volume pro Bin
        bins = [{"low": range_low + i * bin_size, 
                 "high": range_low + (i + 1) * bin_size, 
                 "volume": 0} for i in range(num_bins)]
        
        for d in ohlcv_data:
            vol = d.get("volume", 0)
            h, l = d["high"], d["low"]
            day_range = h - l if h > l else 0.01
            
            for bin in bins:
                overlap_low = max(bin["low"], l)
                overlap_high = min(bin["high"], h)
                if overlap_high > overlap_low:
                    overlap_pct = (overlap_high - overlap_low) / day_range
                    bin["volume"] += vol * overlap_pct
        
        # Durchschnitt berechnen
        avg_vol = sum(b["volume"] for b in bins) / len(bins)
        
        # Voids = Bins mit < 50% des Durchschnitts (konsistent mit Scanner)
        voids = []
        for bin in bins:
            if bin["volume"] < avg_vol * 0.5:
                strength = 1 - (bin["volume"] / avg_vol) if avg_vol > 0 else 1
                voids.append({
                    "price_low": round(bin["low"], 2),
                    "price_high": round(bin["high"], 2),
                    "strength": round(strength, 2)
                })
        
        return voids
    except Exception as e:
        return []


