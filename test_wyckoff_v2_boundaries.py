"""Model migration and chart evidence isolation; no external calls."""
from copy import deepcopy
from datetime import timedelta

import pytest

import api
from modules import patterns
from modules.wyckoff import MODEL
from test_wyckoff_api_integration import candidate
from test_wyckoff_engine import BASE, textbook_bars


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("old_model", ["causal_wyckoff_v1", "causal_wyckoff_v2"])
def test_old_model_cannot_reuse_cached_signal_even_when_other_proof_is_valid(direction, old_model):
    strategy, raw = candidate(direction)
    row = api._apply_pattern_strategy_filter(raw, api.STRATEGIES[strategy])
    assert row and row["wyckoff_model"] == MODEL == "causal_wyckoff_v3"
    row["Strategy"] = strategy
    assert api._stock_wyckoff_row_contract_valid(row, as_of=BASE + timedelta(days=100))
    row["wyckoff_model"] = old_model
    row.update(score=100, grade="S", trade_signal="TRADEABLE")
    assert not api._stock_wyckoff_row_contract_valid(row, as_of=BASE + timedelta(days=100))


def test_other_chart_patterns_cannot_hide_wyckoff_evidence(monkeypatch):
    bars = textbook_bars()
    for bar in bars:
        bar["time"] = int(bar["open_time"].timestamp())
    point = {"time": bars[40]["time"], "price": 96.0}
    proof = {"model": MODEL, "pattern": "Wyckoff Accumulation", "type": "neutral",
             "time": bars[86]["time"], "index_basis": "time", "draw_points": [point],
             "trade_ready": False, "phase": "B", "confidence": "Low",
             "phase_evidence": [{"phase": "B", "start_time": bars[41]["time"]}]}
    others = [{"pattern": f"Other {index}", "type": "neutral", "confidence": "High"}
              for index in range(4)]
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **k: bars)
    monkeypatch.setattr(api, "find_harmonic_for_chart", lambda *a, **k: [])
    monkeypatch.setattr(api, "detect_chart_patterns", lambda *a, **k: deepcopy(others + [proof]))
    monkeypatch.setattr(api, "HAS_PATTERNS", True)
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    result = api.get_chart_data("AAPL", "1D", "patterns", None)
    shown = result["patterns"]["chart_patterns"]
    assert [row for row in shown if row.get("model") == MODEL] == [proof]
    assert len([row for row in shown if row.get("model") != MODEL]) == 3


def test_invalidated_chart_description_does_not_present_live_context():
    bars = textbook_bars(spring=False)
    bars[40].update(open=101., high=102., low=100., close=101., volume=1000.)
    bars[35].update(open=94., high=95., low=89., close=90., volume=1000.)
    shown = patterns.detect_chart_patterns(
        bars, lookback=50,
        wyckoff_context={"bars": bars, "as_of": BASE + timedelta(days=100), "timeframe": "1D"})
    invalid = next(row for row in shown if row.get("model") == MODEL and row["direction"] == "LONG")
    assert invalid["signal_state"] == "invalidated"
    assert "Ungueltige Struktur" in invalid["description"]
    assert invalid["trade"] is None and invalid["target"] is None
    assert "detect_index" not in invalid
