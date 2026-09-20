"""Wyckoff public integration: real engine, fixed clocks, no external I/O."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import inspect
import json
import subprocess

import pytest
import api
from test_wyckoff_engine import BASE, textbook_bars

CUTOFF = BASE + timedelta(days=100)


def candidate(direction="LONG", count=100):
    name = "Wyckoff Accumulation" if direction == "LONG" else "Wyckoff Distribution"
    bars = textbook_bars(direction)[:count]
    return name, {"Strategy": name, "ticker": "OFFLINE", "price": bars[-1]["close"],
                  "Dollar_Volume": 5_000_000, "score": 90, "grade": "S",
                  "_daily_bars": bars, "_pattern_as_of": BASE + timedelta(days=count)}


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_actual_engine_proof_crosses_api_guard_without_overwriting_native_plan(direction):
    name, row = candidate(direction)
    row["trade_setup"] = {"level_model": "native", "entry": 123, "stop": 120}
    before = deepcopy(row["trade_setup"])
    result = api._apply_pattern_strategy_filter(row, api.STRATEGIES[name])
    assert result["wyckoff"]["trade_ready"] is True
    assert result["wyckoff_timeframe"] == "1D"
    assert result["history_days"] == 100
    assert result["trade_setup"] == before  # projection is never the native plan
    assert not any(key.startswith("_") for key in result)
    assert api._stock_wyckoff_row_contract_valid(result, as_of=CUTOFF, expected_strategy=name)
    assert not api._stock_wyckoff_row_contract_valid(result, as_of=CUTOFF,
        expected_strategy="Wyckoff Distribution" if direction == "LONG" else "Wyckoff Accumulation")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("count", [30, 70, 85, 86])
def test_unconfirmed_or_short_history_never_becomes_scanner_signal(direction, count):
    name, row = candidate(direction, count)
    assert api._apply_pattern_strategy_filter(row, api.STRATEGIES[name]) is None


def test_wyckoff_history_adapter_preserves_flags_invalid_prices_and_unknown_rows():
    rows = [{"date": "2026-09-18", "open": 100, "high": float("nan"),
             "low": 99, "close": 100, "volume": 1000, "is_closed": False}, {"bad": True}]
    result = api._stock_wyckoff_daily_input(rows)
    assert len(result) == 2
    assert result[0]["is_closed"] is False
    assert result[0]["close_time"].isoformat() == "2026-09-18T20:00:00+00:00"
    assert result[1] == {"bad": True}


def test_missing_volume_is_diagnosed_before_native_plan_not_deleted():
    name, row = candidate()
    row["_daily_bars"][50].pop("volume")
    reasons = row["_wyckoff_diagnostics"] = {}
    assert api._apply_pattern_strategy_filter(row, api.STRATEGIES[name]) is None
    assert reasons == {"invalid_bar_value": 1}


def test_special_filter_checks_all_wyckoff_candidates_without_provider_refetch(monkeypatch):
    name, row = candidate()
    valid = api._apply_pattern_strategy_filter(row, api.STRATEGIES[name])
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *a: pytest.fail("duplicate provider call"))
    rows = [{**valid, "ticker": f"OFFLINE{i}"} for i in range(231)]
    assert len(api._apply_special_strategy_post_filter(rows, api.STRATEGIES[name], name)) == 231


def test_real_wrapper_rejects_flat_patterns_before_structure_and_only_publishes_empty_preview(monkeypatch):
    from test_stock_momentum_confirmed_contract import _wrapper_fixture, NOW
    written = _wrapper_fixture(monkeypatch, price=100)
    flat = textbook_bars()
    for row in flat:
        row.update(open=100, high=101, low=99, close=100, volume=1_000_000)
    requests, previews = [], []
    def history(ticker, count, *args):
        requests.append(count)
        return flat
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", history)
    monkeypatch.setattr(api, "_strategy_daily_history_metrics", lambda *a, **kw: pytest.fail("unconfirmed pattern reached native analysis"))
    monkeypatch.setattr(api, "save_partial_cache_file", lambda cache, rows, **kw: previews.append(rows))
    rows = api._strategy_scan_wrapper("Wyckoff Accumulation", send_email=False)
    assert rows == [] and requests == [180]
    assert previews and all(preview == [] for preview in previews)
    diagnostic = written[0][1]["metadata"]["diagnostics"]
    assert diagnostic["stage_counts"]["wyckoff_analyzed"] == 1
    assert diagnostic["coverage"] == "complete"
    assert diagnostic["rejected"]["wyckoff:event_sequence_unconfirmed_or_invalid"] == 1


def test_chart_passes_selected_timeframe_and_does_not_remap_causal_event_times(monkeypatch):
    bars = textbook_bars()
    for bar in bars:
        bar["time"] = int(bar["open_time"].timestamp())
    seen = []
    point = {"time": bars[40]["time"], "price": 96., "confirmed_at": "2026-02-12T00:00:00Z"}
    pattern = {"model": "causal_wyckoff_v1", "pattern": "Wyckoff", "type": "bullish",
               "time": bars[86]["time"], "index_basis": "time", "draw_points": [point],
               "trade_ready": False, "phase": "B", "confidence": "Low"}
    def detect(raw, *, lookback, wyckoff_context):
        seen.append(wyckoff_context)
        return [deepcopy(pattern)]
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **k: bars)
    monkeypatch.setattr(api, "find_harmonic_for_chart", lambda *a, **k: [])
    monkeypatch.setattr(api, "detect_chart_patterns", detect)
    monkeypatch.setattr(api, "HAS_PATTERNS", True)
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    result = api.get_chart_data("AAPL", "1D", "patterns", None)
    assert seen[0]["timeframe"] == "1D"
    assert seen[0]["as_of"].tzinfo is not None
    assert len(seen[0]["bars"]) == 100
    assert result["patterns"]["chart_patterns"] == [pattern]


def test_context_and_confirmed_chart_labels_are_not_buy_probability_claims():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    begin = html.index("function chartPatternLabel(")
    end = html.index("function usePublicPlans()", begin)
    script = html[begin:end] + "\nconsole.log(JSON.stringify([chartPatternLabel({model:'causal_wyckoff_v1',pattern:'Wyckoff',timeframe:'4H',phase:'B'}),chartPatternLabel({model:'causal_wyckoff_v1',pattern:'Wyckoff',timeframe:'1D',phase:'D',trade_ready:true})]));"
    labels = json.loads(subprocess.check_output(["node", "-e", script], text=True, encoding="utf-8"))
    assert "4H" in labels[0] and "kein Handelssignal" in labels[0]
    assert "1D" in labels[1] and "Handelsplan separat" in labels[1]
    assert html.count("{chartPatternLabel(p)}") == 2


def test_wyckoff_suppression_reason_keeps_its_own_telemetry_identity():
    assert api._stable_suppression_reason("wyckoff_contract_invalid") == "wyckoff_contract_invalid"
    assert api._alert_decision_from_reasons("stock_strategy", ["wyckoff_contract_invalid"])["decision"] == "NO_TRADE"


@pytest.mark.parametrize("row", [None, 5, "Wyckoff Accumulation", []])
def test_bad_wyckoff_payload_fails_closed_without_exception(row):
    assert not api._stock_wyckoff_row_contract_valid(row, expected_strategy="Wyckoff Accumulation")


def test_chart_wyckoff_cutoff_is_frozen_before_provider_latency(monkeypatch):
    clock = [CUTOFF]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    bars = textbook_bars()
    for bar in bars:
        bar["time"] = int(bar["open_time"].timestamp())
    def fetch(*args, **kwargs):
        clock[0] += timedelta(minutes=15)
        return bars
    seen = []
    def detect(raw, *, lookback, wyckoff_context):
        seen.append(wyckoff_context)
        return []
    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", fetch)
    monkeypatch.setattr(api, "find_harmonic_for_chart", lambda *a, **k: [])
    monkeypatch.setattr(api, "detect_chart_patterns", detect)
    monkeypatch.setattr(api, "HAS_PATTERNS", True)
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    api.get_chart_data("AAPL", "1D", "patterns", None)
    assert seen[0]["as_of"] == CUTOFF


def test_export_includes_only_bounded_wyckoff_counts(tmp_path):
    from scripts.collect_server_evidence import safe_cache_summary
    path = tmp_path / "strategy_wyckoff_accumulation_cache.json"
    path.write_text(json.dumps({"results": [], "timestamp": "2026-09-20T12:00:00",
        "diagnostics": {"stage_counts": {"wyckoff_analyzed": 123, "wyckoff_confirmed": 2},
                        "wyckoff_reasons": {"confirmed": 2, "event_sequence_unconfirmed": 121,
                                            "SECRET_EMAIL_FIXTURE": 5}}}), encoding="utf-8")
    result = safe_cache_summary(path)
    assert result["stage_counts"]["wyckoff_analyzed"] == 123
    assert result["wyckoff_reasons"] == {"confirmed": 2, "event_sequence_unconfirmed": 121, "_omitted_categories": 1}
    assert "SECRET_EMAIL_FIXTURE" not in json.dumps(result)
