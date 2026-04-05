"""
Unit Tests für Trading-Logik — Alpha Station V68.0
=====================================================
Testet die kritischen Scoring-Funktionen auf:
- Mathematische Korrektheit
- Edge Cases (Division by Zero, None-Werte, leere Daten)
- Trading-Logik (würden die besten Trader das so machen?)

WICHTIG: scanner.py importiert streamlit → wir extrahieren die Funktionen
direkt aus dem Source-Code und testen sie isoliert.

Run: python -m pytest test_trading_logic.py -v
"""

import math
import pytest

# =============================================================================
# EXTRAHIERTE FUNKTIONEN (aus scanner.py — ohne streamlit Abhängigkeit)
# =============================================================================

def calculate_close_position(high, low, close, min_range_pct=1.0):
    if high is None or low is None or close is None:
        return None
    if high <= 0 or low <= 0:
        return None
    if high == low:
        return None
    range_pct = ((high - low) / low) * 100
    if range_pct < min_range_pct:
        return None
    close_pos = (close - low) / (high - low)
    return max(0.0, min(1.0, close_pos))


def calculate_alpha_score(rvol, vortag_pct, change_pct):
    rvol_safe = min(max(rvol or 0, 0), 8)
    rvol_score = (math.log(1 + rvol_safe) / math.log(9)) * 35
    change_abs = min(abs(change_pct or 0), 20)
    change_score = (math.sqrt(change_abs) / math.sqrt(20)) * 35
    vortag_abs = min(abs(vortag_pct or 0), 15)
    vortag_score = (vortag_abs / 15) * 30
    return round(rvol_score + vortag_score + change_score, 0)


def _obv_flow_detection(closes, volumes):
    """
    Extrahiert die OBV-Flow-Logik aus analyze_breakout_imminent Signal 3.
    Returns: (obv_rising, obv_falling, early_flow, late_flow)
    """
    n = len(closes)
    obv = [0]
    for i in range(1, n):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])

    if len(obv) < 6:
        return None, None, 0, 0

    mid = len(obv) // 2
    early_flow = obv[mid] - obv[0]
    late_flow = obv[-1] - obv[mid]
    obv_rising = late_flow > 0 and (early_flow <= 0 or late_flow > early_flow * 0.5)
    obv_falling = late_flow < 0 and (early_flow >= 0 or late_flow < early_flow * 0.5)
    return obv_rising, obv_falling, early_flow, late_flow


def _ccd_flow_detection(closes):
    """
    Extrahiert die CCD-Flow-Logik (Crypto OBV-Proxy) aus analyze_breakout_imminent.
    Returns: (ccd_rising, ccd_falling, early_flow, late_flow)
    """
    n = len(closes)
    ccd = [0]
    for i in range(1, n):
        ccd.append(ccd[-1] + (closes[i] - closes[i-1]))

    if len(ccd) < 6:
        return None, None, 0, 0

    mid = len(ccd) // 2
    early_flow = ccd[mid] - ccd[0]
    late_flow = ccd[-1] - ccd[mid]
    ccd_rising = late_flow > 0 and (early_flow <= 0 or late_flow > early_flow * 0.5)
    ccd_falling = late_flow < 0 and (early_flow >= 0 or late_flow < early_flow * 0.5)
    return ccd_rising, ccd_falling, early_flow, late_flow


def _obv_trend_multiday(closes, volumes):
    """
    Extrahiert die OBV-Trend-Logik aus analyze_multi_day_pattern.
    Returns: obv_trend (positive = accumulation, negative = distribution)
    """
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])

    if len(obv) < 4:
        return 0

    mid = len(obv) // 2
    early_flow = obv[mid] - obv[0]
    late_flow = obv[-1] - obv[mid]
    return late_flow


# =============================================================================
# TEST: OBV FLOW DETECTION (Der kritische Fix!)
# =============================================================================

class TestOBVFlowDetection:
    """Tests für die OBV-Divergenz-Erkennung im BI Scanner."""

    def test_pure_accumulation(self):
        """Stetig steigendes OBV = Akkumulation → obv_rising=True"""
        # 10 Tage, Preis steigt jeden Tag, Volume 100k
        closes = [100 + i for i in range(10)]  # 100, 101, ..., 109
        volumes = [100000] * 10
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        assert rising is True, "Pure Akkumulation muss obv_rising sein"
        assert falling is False
        assert early > 0
        assert late > 0

    def test_pure_distribution(self):
        """Stetig fallendes OBV = Distribution → obv_falling=True"""
        # 10 Tage, Preis fällt jeden Tag
        closes = [110 - i for i in range(10)]  # 110, 109, ..., 101
        volumes = [100000] * 10
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        assert falling is True, "Pure Distribution muss obv_falling sein"
        assert rising is False

    def test_accumulation_then_distribution_THE_CRITICAL_BUG(self):
        """
        🔴 DER KRITISCHE BUG-TEST:
        OBV steigt 10 Tage dann fällt 10 Tage → MUSS als FALLING erkannt werden.

        Die ALTE Logik (Level-Durchschnitte) hätte hier FÄLSCHLICHERWEISE
        "obv_rising" gemeldet weil die kumulative 2. Hälfte höhere absolute
        Werte hat, obwohl OBV gerade FÄLLT.
        """
        # 10 Up-Tage dann 10 Down-Tage
        closes_up = [100 + i for i in range(10)]     # 100→109
        closes_down = [109 - i for i in range(10)]    # 109→100
        closes = closes_up + closes_down
        volumes = [100000] * 20

        rising, falling, early, late = _obv_flow_detection(closes, volumes)

        # Der late_flow MUSS negativ sein (Distribution in 2. Hälfte)
        assert late < 0, f"Late flow muss negativ sein bei Distribution, war {late}"
        assert falling is True, "Aktie wird distribuiert — muss als falling erkannt werden"
        assert rising is False, "DARF NICHT als rising erkannt werden (alter Bug!)"

    def test_distribution_then_accumulation(self):
        """OBV fällt dann steigt → obv_rising=True (Reversal zu Akkumulation)"""
        closes_down = [110 - i for i in range(10)]  # 110→101
        closes_up = [101 + i for i in range(10)]     # 101→110
        closes = closes_down + closes_up
        volumes = [100000] * 20

        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        assert late > 0, "Late flow muss positiv sein nach Reversal"
        assert rising is True

    def test_flat_obv(self):
        """Abwechselnd up/down gleich stark → kein klares Signal"""
        closes = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101]
        volumes = [100000] * 10
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        # Bei perfektem Chop sollte weder rising noch falling sein
        # (oder beides False weil der Flow nahe 0 ist)
        assert not (rising and falling), "Kann nicht gleichzeitig rising und falling sein"

    def test_strong_late_accumulation_after_weak_early(self):
        """Schwache erste Hälfte, starke zweite → rising"""
        # Erste Hälfte: leicht steigend mit kleinem Volume
        closes = [100, 100.5, 101, 101.5, 102, 102, 103, 104, 106, 109]
        volumes = [50000, 50000, 50000, 50000, 50000, 200000, 200000, 200000, 200000, 200000]
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        assert rising is True, "Starke späte Akkumulation muss erkannt werden"

    def test_minimum_data(self):
        """Weniger als 6 Datenpunkte → None"""
        closes = [100, 101, 102, 103, 104]
        volumes = [100000] * 5
        rising, falling, _, _ = _obv_flow_detection(closes, volumes)
        assert rising is None
        assert falling is None


# =============================================================================
# TEST: CCD FLOW DETECTION (Crypto OBV-Proxy)
# =============================================================================

class TestCCDFlowDetection:
    """Tests für Crypto Cumulative Close Delta."""

    def test_accumulation_then_distribution(self):
        """Gleicher kritischer Test wie OBV — CCD muss Distribution erkennen"""
        closes_up = [100 + i * 2 for i in range(10)]     # 100→118
        closes_down = [118 - i * 2 for i in range(10)]    # 118→100
        closes = closes_up + closes_down
        rising, falling, early, late = _ccd_flow_detection(closes)
        assert late < 0, "CCD late flow muss negativ sein bei Distribution"
        assert falling is True
        assert rising is False

    def test_pure_uptrend(self):
        """Stetig steigende Preise → ccd_rising"""
        closes = [100 + i for i in range(10)]
        rising, falling, early, late = _ccd_flow_detection(closes)
        assert rising is True

    def test_pure_downtrend(self):
        """Stetig fallende Preise → ccd_falling"""
        closes = [110 - i for i in range(10)]
        rising, falling, early, late = _ccd_flow_detection(closes)
        assert falling is True


# =============================================================================
# TEST: OBV TREND (Multi-Day Pattern Analysis)
# =============================================================================

class TestOBVTrendMultiDay:
    """Tests für OBV-Trend in Wyckoff/Consolidation Patterns."""

    def test_accumulation_positive_trend(self):
        """Steigendes OBV → positiver Trend"""
        closes = [100 + i for i in range(10)]
        volumes = [100000] * 10
        trend = _obv_trend_multiday(closes, volumes)
        assert trend > 0, "Akkumulation muss positiven Trend zeigen"

    def test_distribution_negative_trend(self):
        """Fallendes OBV → negativer Trend"""
        closes = [110 - i for i in range(10)]
        volumes = [100000] * 10
        trend = _obv_trend_multiday(closes, volumes)
        assert trend < 0, "Distribution muss negativen Trend zeigen"

    def test_peak_then_decline(self):
        """OBV peak in Mitte, dann Decline → MUSS negativen Trend zeigen"""
        closes_up = [100 + i for i in range(10)]
        closes_down = [109 - i for i in range(10)]
        closes = closes_up + closes_down
        volumes = [100000] * 20
        trend = _obv_trend_multiday(closes, volumes)
        assert trend < 0, f"OBV peaked und fällt — Trend MUSS negativ sein, war {trend}"


# =============================================================================
# TEST: CALCULATE CLOSE POSITION
# =============================================================================

class TestClosePosition:
    """Tests für Close Position Berechnung."""

    def test_close_at_high(self):
        """Close am High → 1.0"""
        result = calculate_close_position(110, 100, 110)
        assert result == 1.0

    def test_close_at_low(self):
        """Close am Low → 0.0"""
        result = calculate_close_position(110, 100, 100)
        assert result == 0.0

    def test_close_at_midpoint(self):
        """Close in der Mitte → 0.5"""
        result = calculate_close_position(110, 100, 105)
        assert result == 0.5

    def test_no_range(self):
        """High == Low → None"""
        result = calculate_close_position(100, 100, 100)
        assert result is None

    def test_tiny_range(self):
        """Range < min_range_pct → None"""
        result = calculate_close_position(100.1, 100.0, 100.05, min_range_pct=1.0)
        assert result is None

    def test_none_inputs(self):
        """None-Werte → None"""
        assert calculate_close_position(None, 100, 105) is None
        assert calculate_close_position(110, None, 105) is None
        assert calculate_close_position(110, 100, None) is None

    def test_close_above_high(self):
        """Close über High (z.B. After-Hours) → clamped auf 1.0"""
        result = calculate_close_position(110, 100, 115)
        assert result == 1.0

    def test_close_below_low(self):
        """Close unter Low → clamped auf 0.0"""
        result = calculate_close_position(110, 100, 95)
        assert result == 0.0

    def test_zero_prices(self):
        """High/Low bei 0 → None"""
        assert calculate_close_position(0, 0, 0) is None
        assert calculate_close_position(10, 0, 5) is None


# =============================================================================
# TEST: CALCULATE ALPHA SCORE
# =============================================================================

class TestAlphaScore:
    """Tests für Alpha Score Berechnung."""

    def test_zero_inputs(self):
        """Alle Null → Score 0"""
        score = calculate_alpha_score(0, 0, 0)
        assert score == 0

    def test_maximum_inputs(self):
        """Maximale Werte → Score nahe 100"""
        score = calculate_alpha_score(8, 15, 20)
        assert score == 100, f"Maximale Inputs müssen 100 ergeben, war {score}"

    def test_none_inputs(self):
        """None-Werte → Score 0 (graceful handling)"""
        score = calculate_alpha_score(None, None, None)
        assert score == 0

    def test_rvol_logarithmic(self):
        """RVOL Scoring ist logarithmisch → erste 2x zählen mehr als 6x→8x"""
        score_2x = calculate_alpha_score(2, 0, 0)
        score_4x = calculate_alpha_score(4, 0, 0)
        score_8x = calculate_alpha_score(8, 0, 0)
        # Differenz 0→2 sollte größer sein als 4→8
        diff_low = score_2x - 0
        diff_high = score_8x - score_4x
        assert diff_low > diff_high, "RVOL muss logarithmisch sein (diminishing returns)"

    def test_change_sqrt(self):
        """Change% Scoring ist sqrt → moderate Moves zählen mehr"""
        score_5pct = calculate_alpha_score(0, 0, 5)
        score_10pct = calculate_alpha_score(0, 0, 10)
        score_20pct = calculate_alpha_score(0, 0, 20)
        # 0→5% Unterschied sollte größer sein als 10→20%
        diff_low = score_5pct
        diff_high = score_20pct - score_10pct
        assert diff_low > diff_high, "Change% muss sqrt sein (moderate Moves wichtiger)"

    def test_negative_change_same_as_positive(self):
        """Alpha Score ist direction-blind (by design)"""
        score_pos = calculate_alpha_score(2, 3, 5)
        score_neg = calculate_alpha_score(2, 3, -5)
        assert score_pos == score_neg, "Alpha Score muss direction-blind sein"

    def test_rvol_capped_at_8(self):
        """RVOL über 8 bringt keine Extra-Punkte"""
        score_8 = calculate_alpha_score(8, 0, 0)
        score_100 = calculate_alpha_score(100, 0, 0)
        assert score_8 == score_100, "RVOL > 8 muss gecapped sein"

    def test_score_range(self):
        """Score muss immer 0-100 sein"""
        # Extreme Werte testen
        for rvol in [0, 1, 5, 10, 100]:
            for vortag in [-50, -5, 0, 5, 50]:
                for change in [-50, -5, 0, 5, 50]:
                    score = calculate_alpha_score(rvol, vortag, change)
                    assert 0 <= score <= 100, f"Score {score} out of range für rvol={rvol}, vortag={vortag}, change={change}"

    def test_typical_breakout(self):
        """Typischer Breakout: RVOL 3x, Change 5%, Vortag 1%"""
        score = calculate_alpha_score(3, 1, 5)
        assert 30 <= score <= 60, f"Typischer Breakout sollte moderate Score haben, war {score}"

    def test_monster_move(self):
        """Monster Move: RVOL 7x, Change 15%, Vortag 8%"""
        score = calculate_alpha_score(7, 8, 15)
        assert score >= 75, f"Monster Move sollte hohen Score haben, war {score}"


# =============================================================================
# TEST: EDGE CASES UND MATHEMATISCHE GRENZEN
# =============================================================================

class TestEdgeCases:
    """Tests für mathematische Edge Cases."""

    def test_obv_all_flat_closes(self):
        """Alle Closes gleich → OBV bleibt bei 0"""
        closes = [100.0] * 10
        volumes = [100000] * 10
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        assert early == 0
        assert late == 0
        assert rising is False or rising is None
        assert falling is False or falling is None

    def test_obv_single_big_volume_day(self):
        """Ein Tag mit 100x Volume → sollte nicht alles dominieren"""
        closes = [100, 101, 102, 103, 104, 105, 104, 103, 102, 101]
        volumes = [100, 100, 100, 100, 10000000, 100, 100, 100, 100, 100]
        # Der eine große Volume-Tag in der Mitte sollte den early_flow dominieren
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        # Late period ist fallend (104→101 mit kleinem Volume)
        assert late < 0, "Späte Distribution muss erkannt werden"

    def test_obv_zero_volume(self):
        """Zero Volume Tage → OBV bleibt gleich"""
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
        volumes = [0] * 10
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        assert early == 0
        assert late == 0

    def test_close_position_penny_stock(self):
        """Penny Stock $0.50 mit kleiner Range"""
        result = calculate_close_position(0.55, 0.45, 0.53, min_range_pct=1.0)
        assert result is not None  # Range > 1%
        assert 0.7 < result < 0.9  # Close nahe High

    def test_close_position_high_price_stock(self):
        """Berkshire Hathaway ($600k) mit minimaler Range"""
        result = calculate_close_position(600100, 600000, 600080, min_range_pct=1.0)
        # Range = $100 / $600000 = 0.017% → unter 1% → None
        assert result is None


# =============================================================================
# TEST: TRADING LOGIC SCENARIOS (Realistische Szenarien)
# =============================================================================

class TestTradingScenarios:
    """Tests mit realistischen Trading-Szenarien."""

    def test_wyckoff_accumulation_scenario(self):
        """
        Wyckoff Akkumulation: Preis seitwärts, OBV steigt leise.
        Smart Money kauft in der Range → OBV muss steigend erkannt werden.
        """
        # Preis pendelt um $50, aber Volume ist höher an Up-Tagen
        closes = [50, 50.5, 49.8, 50.2, 49.9, 50.3, 50.1, 50.5, 50.0, 50.4]
        volumes = [100, 200, 80, 180, 90, 250, 100, 220, 85, 200]
        # Up-Tage haben mehr Volume = Akkumulation
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        assert rising is True, "Wyckoff Akkumulation muss als rising erkannt werden"

    def test_distribution_topping_pattern(self):
        """
        Distribution: Aktie war im Uptrend, jetzt seitwärts mit
        hohem Volume an Down-Tagen → Smart Money verkauft.
        """
        closes = [100, 99, 100.5, 98.5, 100, 99.5, 98, 100, 97, 99]
        volumes = [100, 300, 80, 350, 100, 280, 400, 90, 500, 120]
        # Down-Tage haben viel mehr Volume = Distribution
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        assert falling is True, "Distribution Topping muss als falling erkannt werden"

    def test_breakout_day_high_volume(self):
        """
        Breakout: Konsolidierung dann plötzlich Up mit massivem Volume.
        """
        closes = [50, 50.1, 49.9, 50.0, 50.2, 49.8, 50.0, 50.1, 52, 55]
        volumes = [100, 100, 100, 100, 100, 100, 100, 100, 500, 1000]
        rising, falling, early, late = _obv_flow_detection(closes, volumes)
        assert late > 0, "Breakout-Volume muss positiven late_flow erzeugen"
        assert rising is True

    def test_alpha_score_sorting_makes_sense(self):
        """
        Alpha Score Sortierung: Breakout mit RVOL 5x, +8%
        muss höher ranken als ruhige Aktie mit RVOL 0.5x, +0.5%
        """
        breakout = calculate_alpha_score(5, 2, 8)
        boring = calculate_alpha_score(0.5, 0.3, 0.5)
        assert breakout > boring * 2, f"Breakout ({breakout}) muss deutlich über boring ({boring}) ranken"


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
