import pytest

from modules.premarket import _simulate_pm_setup


SETUP_LONG = {"entry": 10.0, "stop": 9.0, "tp1": 11.5, "tp2": 13.0}


def test_pm_tracker_realizes_tp1_before_runner_stops():
    bars = [
        {"o": 9.8, "h": 10.4, "l": 9.5, "c": 10.2},
        {"o": 10.2, "h": 11.7, "l": 10.1, "c": 11.6},
        {"o": 11.5, "h": 11.6, "l": 8.9, "c": 9.1},
    ]
    result = _simulate_pm_setup(bars, SETUP_LONG, "LONG", fee_pct=0.0)
    assert result["exit_reason"] == "TP1_STOP"
    assert result["tp1_hit"] is True
    assert result["stop_hit"] is True
    assert result["exit_price"] == pytest.approx(10.25)
    assert result["r_multiple"] == pytest.approx(0.25)


def test_pm_tracker_uses_half_tp1_half_tp2_and_net_fees():
    bars = [
        {"o": 9.9, "h": 10.3, "l": 9.5, "c": 10.1},
        {"o": 10.2, "h": 13.2, "l": 10.1, "c": 13.0},
    ]
    result = _simulate_pm_setup(bars, SETUP_LONG, "LONG", fee_pct=0.25)
    assert result["exit_reason"] == "TP2"
    assert result["exit_price"] == pytest.approx(12.25)
    assert result["pnl_pct"] == pytest.approx(22.25)
    assert result["r_multiple"] == pytest.approx(2.225)


def test_pm_tracker_rejects_gap_past_first_target():
    bars = [{"o": 11.8, "h": 12.0, "l": 11.6, "c": 11.9}]
    result = _simulate_pm_setup(bars, SETUP_LONG, "LONG")
    assert result["entry_hit"] is False
    assert result["exit_reason"] == "GAP PAST TP1 - NO ENTRY"


def test_pm_tracker_short_geometry_and_partial_exit_are_directional():
    setup = {"entry": 10.0, "stop": 11.0, "tp1": 8.5, "tp2": 7.0}
    bars = [
        {"o": 10.2, "h": 10.4, "l": 9.8, "c": 9.9},
        {"o": 9.8, "h": 9.9, "l": 8.4, "c": 8.5},
        {"o": 8.6, "h": 11.1, "l": 8.4, "c": 10.9},
    ]
    result = _simulate_pm_setup(bars, setup, "SHORT", fee_pct=0.0)
    assert result["exit_reason"] == "TP1_STOP"
    assert result["exit_price"] == pytest.approx(9.75)
    assert result["r_multiple"] == pytest.approx(0.25)
