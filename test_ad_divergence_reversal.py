"""Tests: Chaikin-A/D-Divergenz-Filter fuer die Trend-Reversal-Strategie.

Fachliche Basis (Chaikin-Schule):
- Preis faellt, A/D-Linie haelt/steigt => Akkumulation unter der Oberflaeche
  => qualitativ starkes Reversal-Setup (BULLISH_DIVERGENCE, Score-Bonus +8).
- A/D bestaetigt den Abverkauf mit eigenem Lower Low + deutlich negativer
  Steigung => Distribution => Falling Knife => Reject.
- Datenmangel blockt NIE (NEUTRAL/insufficient_history => Pass ohne Bonus).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.indicators import calculate_ad_line
from api import (
    _ad_divergence_signal,
    _stock_reversal_ad_gate,
    resolve_strategy_name,
)


# ---------------------------------------------------------------------------
# Synthetische Bar-Serien (Aufruf-Konventionen wie test_stock_strategy_momentum_gate)
# ---------------------------------------------------------------------------

def _bullish_divergence_bars(n=20):
    """Preis -8% ueber n Tage (Lower Lows), aber Up-Tage mit Close nahe High
    und 5x Volumen => A/D-Linie steigt netto = Akkumulation."""
    bars = []
    for i in range(n):
        base = 100.0 - 0.42 * i
        if i % 2 == 1:  # gruener Tag: Close nahe High, dickes Volumen
            bars.append({"high": base + 0.2, "low": base - 1.5, "close": base, "volume": 3_000_000})
        else:           # roter Tag: Close mittig-oben, duennes Volumen
            bars.append({"high": base + 1.2, "low": base - 0.3, "close": base, "volume": 600_000})
    return bars


def _distribution_bars(n=20):
    """Preis faellt -19%, jeden Tag Close nahe Low bei hohem Volumen
    => A/D faellt steil mit = Falling Knife."""
    return [
        {"high": (100.0 - i) + 2.0, "low": (100.0 - i) - 0.2, "close": 100.0 - i, "volume": 2_000_000}
        for i in range(n)
    ]


def _sideways_bars(n=20):
    """Seitwaerts, Close in der Mitte => A/D flach, keine Divergenz."""
    return [{"high": 101.0, "low": 99.0, "close": 100.0, "volume": 1_000_000} for _ in range(n)]


# ---------------------------------------------------------------------------
# 1) A/D-Linie (modules/indicators.py)
# ---------------------------------------------------------------------------

def test_ad_line_hand_calculation_three_bars_with_doji():
    bars = [
        # MFM = ((12-10)-(12-12))/(12-10) = +1.0  -> AD = +1000
        {"high": 12.0, "low": 10.0, "close": 12.0, "volume": 1000},
        # MFM = ((12-12)-(14-12))/(14-12) = -1.0  -> AD = 1000 - 2000 = -1000
        {"high": 14.0, "low": 12.0, "close": 12.0, "volume": 2000},
        # Doji H==L => MFM = 0 (kein ZeroDivision-Crash) -> AD bleibt -1000
        {"high": 10.0, "low": 10.0, "close": 10.0, "volume": 5000},
    ]
    assert calculate_ad_line(bars) == [1000.0, -1000.0, -1000.0]


def test_ad_line_empty_and_too_short_inputs():
    assert calculate_ad_line([]) == []
    assert calculate_ad_line(None) == []
    assert calculate_ad_line([{"high": 10.0, "low": 9.0, "close": 9.5, "volume": 100}]) == []


def test_ad_line_skips_broken_bars_and_carries_value_forward():
    bars = [
        {"high": 12.0, "low": 10.0, "close": 12.0, "volume": 1000},  # AD = +1000
        None,                                                         # defekt -> fortschreiben
        {"high": 11.0, "low": 10.0, "close": float("nan"), "volume": 500},  # NaN -> fortschreiben
        {"high": 12.0, "low": 10.0, "close": 10.0, "volume": 1000},  # MFM=-1 -> AD = 0
    ]
    ad = calculate_ad_line(bars)
    assert len(ad) == len(bars)
    assert ad == [1000.0, 1000.0, 1000.0, 0.0]


# ---------------------------------------------------------------------------
# 2) Divergenz-Klassifikation (_ad_divergence_signal)
# ---------------------------------------------------------------------------

def test_bullish_divergence_detected_on_lower_low_with_accumulation():
    sig = _ad_divergence_signal(_bullish_divergence_bars())
    assert sig["signal"] == "BULLISH_DIVERGENCE"
    assert sig["price_lower_low"] is True
    assert sig["ad_higher_low"] is True
    assert sig["ad_slope_norm"] > 0
    assert sig["reason"] == "price_weak_but_ad_accumulating"


def test_distribution_detected_when_ad_confirms_selloff():
    sig = _ad_divergence_signal(_distribution_bars())
    assert sig["signal"] == "DISTRIBUTION"
    assert sig["price_lower_low"] is True
    assert sig["ad_higher_low"] is False
    # A/D gibt klar mehr als 2 mittlere Tagesvolumina ab (deutlich negativ)
    assert sig["ad_slope_norm"] <= -2.0
    assert sig["reason"] == "ad_confirms_selloff"


def test_neutral_on_sideways_market():
    sig = _ad_divergence_signal(_sideways_bars())
    assert sig["signal"] == "NEUTRAL"
    assert sig["reason"] == "no_clear_divergence"


def test_insufficient_history_returns_neutral_not_block():
    sig = _ad_divergence_signal(_bullish_divergence_bars(8))
    assert sig["signal"] == "NEUTRAL"
    assert sig["reason"] == "insufficient_history"


def test_running_day_bar_is_excluded_from_window():
    """Konvention der Umgebung: laufender Tag zaehlt nicht als kompletter Bar.

    19 komplette Bars + 1 Bar mit heutigem Datum => effektiv 19 < lookback
    => insufficient_history (der heutige Bar darf das Fenster nicht fuellen).
    """
    from datetime import datetime, timezone

    bars = _bullish_divergence_bars(19)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bars.append({"high": 95.0, "low": 90.0, "close": 91.0, "volume": 9_000_000, "date": today})
    sig = _ad_divergence_signal(bars)
    assert sig["signal"] == "NEUTRAL"
    assert sig["reason"] == "insufficient_history"


# ---------------------------------------------------------------------------
# 3) Reversal-Gate (_stock_reversal_ad_gate)
# ---------------------------------------------------------------------------

def test_gate_passes_bullish_divergence_with_bonus_and_row_flags():
    row = {"score": 70, "grade": "A", "base_score": 70, "base_grade": "A"}
    ok, reasons, ad_info = _stock_reversal_ad_gate("Trend Reversal", _bullish_divergence_bars(), row)

    assert ok
    assert reasons == []
    assert ad_info["signal"] == "BULLISH_DIVERGENCE"
    assert row["ad_divergence"] is True
    assert row["ad_signal"] == "BULLISH_DIVERGENCE"
    assert row["score"] == 78  # +8 Akkumulations-Bonus
    assert row["base_score"] == 78
    assert row["grade"] == "A"


def test_gate_rejects_distribution_as_falling_knife():
    ok, reasons, ad_info = _stock_reversal_ad_gate("Trend Reversal", _distribution_bars(), {"score": 70})

    assert not ok
    assert "ad_confirms_selloff_falling_knife" in reasons
    assert ad_info["signal"] == "DISTRIBUTION"


def test_gate_passes_neutral_without_bonus():
    row = {"score": 70, "grade": "A"}
    ok, reasons, ad_info = _stock_reversal_ad_gate("Trend Reversal", _sideways_bars(), row)

    assert ok
    assert reasons == []
    assert row["score"] == 70  # kein Bonus
    assert row["ad_signal"] == "NEUTRAL"
    assert row["ad_divergence"] is False


def test_gate_never_blocks_on_insufficient_history():
    row = {"score": 70, "grade": "A"}
    ok, reasons, ad_info = _stock_reversal_ad_gate("Trend Reversal", _bullish_divergence_bars(8), row)

    assert ok
    assert reasons == []
    assert ad_info["reason"] == "insufficient_history"
    assert row["ad_signal"] == "NEUTRAL"
    assert row["score"] == 70


def test_gate_only_applies_to_reversal_strategy():
    """Momentum Breakout Long laeuft ohne AD-Logik durch — selbst bei
    haerter Distribution bleibt das Gate neutral/ok und die Row unberuehrt."""
    row = {"score": 70}
    ok, reasons, ad_info = _stock_reversal_ad_gate("Momentum Breakout Long", _distribution_bars(), row)

    assert ok
    assert reasons == []
    assert ad_info == {}
    assert row == {"score": 70}  # keine ad_* Felder, kein Bonus


def test_gate_score_bonus_is_capped_at_100():
    row = {"score": 95, "grade": "S", "base_score": 95, "base_grade": "S"}
    ok, _, _ = _stock_reversal_ad_gate("Trend Reversal", _bullish_divergence_bars(), row)

    assert ok
    assert row["score"] == 100  # 95 + 8 => Cap bei 100, nicht 103
    assert row["base_score"] == 100
    assert row["grade"] == "S"


def test_reversal_hunter_alias_resolves_to_trend_reversal():
    """Live-Pfad: der Scan kanonisiert Aliasse VOR der Schleife — das Gate
    sieht immer den kanonischen Namen (gleiche Konvention wie Momentum-Gate)."""
    assert resolve_strategy_name("Reversal Hunter") == "Trend Reversal"
