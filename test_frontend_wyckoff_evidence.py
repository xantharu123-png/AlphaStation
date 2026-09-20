"""Execute the shipped Wyckoff chart projection without network or providers."""
import copy
import json
from pathlib import Path

import pytest

from test_frontend_scanner_lifecycle import node_run

SOURCE = (Path(__file__).parent / "frontend/index.html").read_text(encoding="utf-8")
PURE = SOURCE[SOURCE.index("function isWyckoffPattern("):SOURCE.index("function usePublicPlans(")]


def evaluate(expression):
    return json.loads(node_run(PURE + "\nconsole.log(JSON.stringify(" + expression + "));"))


def fixture():
    base = 1767225600
    times = [base + index * 86400 for index in range(12)]
    def event(name, index, confirmed, price, phase):
        return dict(name=name, time=times[index], confirmation_time=times[confirmed], price=price,
                    observed_at=f"2026-01-{index + 1:02d}T21:00:00Z",
                    confirmed_at=f"2026-01-{confirmed + 1:02d}T21:00:00Z", phase=phase,
                    volume_ratio=1.5)
    pattern = dict(model="causal_wyckoff_v2", pattern="Wyckoff Akkumulation", type="bullish",
                   direction="LONG", timeframe="1D", signal_state="context", trade_ready=False,
                   phase="B", range_low=90, range_high=110, range_start_time=times[0],
                   range_end_time=times[-1], range_confirmed_time=times[3],
                   event_evidence=[event("SC", 0, 0, 90, "A"), event("AR", 2, 3, 110, "A"),
                                   event("ST", 5, 6, 91, "A"), event("ST", 8, 9, 92, "B")],
                   phase_evidence=[dict(phase="A", start_time=times[0], end_time=times[6],
                                        confirmed_at="2026-01-07T21:00:00Z", status="confirmed"),
                                   dict(phase="B", start_time=times[6], end_time=times[-1],
                                        confirmed_at="2026-01-07T21:00:00Z", status="context")])
    candles = [dict(time=time, low=85, high=115, open=100, close=102) for time in times]
    return pattern, candles


def project(pattern=None, candles=None, timeframe="1D"):
    default_pattern, default_candles = fixture()
    return evaluate("projectWyckoffChartEvidence(" + ",".join(json.dumps(value) for value in
                    [pattern or default_pattern, candles if candles is not None else default_candles, timeframe]) + ")")


def test_observed_points_and_bounded_range_begin_only_at_confirmation():
    pattern, candles = fixture()
    result = project()
    assert len(result["points"]) == 4
    assert all(point["color"] == "#94a3b8" for point in result["points"])
    assert [line["name"] for line in result["rangeLines"]] == ["Unterstuetzung", "Widerstand"]
    assert result["rangeLines"][0]["data"] == [dict(time=candles[3]["time"], value=90),
                                                dict(time=candles[-1]["time"], value=90)]
    assert result["markers"][0]["text"] == "1 SC"
    assert result["points"][0]["observed_at"] == "2026-01-01T21:00:00Z"
    assert result["points"][0]["price"] == 90


def test_phase_and_event_numbering_is_not_a_wave_count_or_invented_phase_c():
    pattern, _ = fixture()
    evidence = evaluate("wyckoffEvidence(" + json.dumps(pattern) + ")")
    assert [event["sequence"] for event in evidence["events"]] == [1, 2, 3, 4]
    assert [event["label"] for event in evidence["events"]] == ["SC", "AR", "ST #1", "ST #2"]
    assert evidence["events"][2]["phaseLabel"] == "A"
    assert evidence["events"][3]["phaseLabel"] == "B"
    phases = {row["phase"]: row for row in evidence["phases"]}
    assert phases["C"]["label"] == "nicht belegt"
    assert phases["E"]["label"] == "nicht modelliert"


@pytest.mark.parametrize("state,ready,color", [("context", False, "#94a3b8"),
    ("invalidated", True, "#94a3b8"), ("confirmed", True, "#10b981")])
def test_invalid_and_context_are_not_colored_as_confirmed_bullish(state, ready, color):
    pattern, _ = fixture()
    pattern.update(signal_state=state, trade_ready=ready)
    assert {row["color"] for row in project(pattern)["points"]} == {color}


def test_distribution_is_red_only_if_confirmed_and_never_connected():
    pattern, _ = fixture()
    pattern.update(direction="SHORT", type="bearish", trade_ready=True, signal_state="confirmed")
    assert {row["color"] for row in project(pattern)["points"]} == {"#ef4444"}
    script = PURE + "\nconst assert=require('assert');const calls=[];const markers=[];const series=[];"
    script += "renderWyckoffChartEvidence({pattern:" + json.dumps(pattern) + ",candles:" + json.dumps(fixture()[1]) + """,timeframe:'1D',series,markers,
      LineStyle:{Dashed:2},chart:{addLineSeries(options){const call={options};calls.push(call);return{setData(data){call.data=data;}};}}});
      assert.equal(calls.length,6);assert.equal(markers.length,4);
      for(const call of calls.slice(0,4)){assert.equal(call.options.lineVisible,false);assert.equal(call.data.length,1);}
      for(const call of calls.slice(4)){assert.equal(call.data.length,2);assert(call.data[0].time<call.data[1].time);}
    """
    node_run(script)


def test_two_events_on_one_candle_keep_two_prices_without_duplicate_series_timestamps():
    pattern, _ = fixture()
    duplicate = copy.deepcopy(pattern["event_evidence"][2])
    duplicate.update(name="Spring", price=89, phase="C")
    pattern["event_evidence"].append(duplicate)
    result = project(pattern)
    assert len(result["points"]) == 5 and len(result["markers"]) == 4
    assert [row["price"] for row in result["points"] if row["time"] == duplicate["time"]] == [91, 89]
    assert any("ST #1" in row["text"] and "Spring" in row["text"] for row in result["markers"])


@pytest.mark.parametrize("case", ["missing", "duplicate", "bad_price", "price_outside_bar", "future_confirmation"])
def test_unmatched_event_proof_does_not_become_a_chart_point(case):
    pattern, candles = fixture()
    event = pattern["event_evidence"][0]
    if case == "missing": candles.pop(0)
    elif case == "duplicate": candles.insert(0, copy.deepcopy(candles[0]))
    elif case == "bad_price": event["price"] = "90"
    elif case == "price_outside_bar": event["price"] = 1000
    else: event["confirmation_time"] = candles[-1]["time"] + 86400
    assert len(project(pattern, candles)["points"]) == 3


def test_other_timeframe_never_projects_daily_event_points_or_range():
    assert project(timeframe="4H") == dict(points=[], markers=[], rangeLines=[])


def test_bad_range_or_unconfirmed_start_never_creates_a_boundary():
    pattern, _ = fixture()
    pattern["range_confirmed_time"] = pattern["range_end_time"]
    assert project(pattern)["rangeLines"] == []
    pattern["range_confirmed_time"] = fixture()[0]["range_confirmed_time"]
    pattern["range_low"] = 120
    assert project(pattern)["rangeLines"] == []


def test_saved_daily_scanner_evidence_and_current_chart_are_distinct():
    pattern, _ = fixture()
    stored = copy.deepcopy(pattern)
    del stored["model"]
    current = copy.deepcopy(pattern)
    current["timeframe"] = "4H"
    row = dict(scanner="Wyckoff Accumulation", wyckoff=stored, wyckoff_model="causal_wyckoff_v2")
    result = evaluate("wyckoffDisplayRows(" + json.dumps([current]) + "," + json.dumps(row) + ",'LONG')")
    assert len(result) == 2
    assert result[0]["stored"] is True and result[0]["pattern"]["timeframe"] == "1D"
    assert result[1]["stored"] is False and result[1]["pattern"]["timeframe"] == "4H"
    assert "keine aktuelle Neubestaetigung" in result[0]["source"]


def test_both_charts_use_shared_no_zigzag_renderer_and_accessible_phase_panel():
    assert SOURCE.count("renderWyckoffChartEvidence({ pattern: p, candles, timeframe") == 2
    assert SOURCE.count("<WyckoffPatternEvidence patterns=") == 2
    assert "keine Elliott-Wellen und keine feste A1–A5-Regel" in SOURCE
    assert "ST #1 / #2 bezeichnet wiederholte Tests" in SOURCE
    assert 'aria-label="Wyckoff-Ereignistabelle"' in SOURCE
    assert 'scope="col"' in SOURCE and 'scope="row"' in SOURCE
    assert "function CupPatternEvidence(" in SOURCE
    assert "markers.push(...cupEvidence.markers)" in SOURCE


def test_missing_phase_proof_does_not_paint_a_complete_phase_history():
    pattern, _ = fixture()
    pattern.pop("phase_evidence")
    rows = evaluate("wyckoffEvidence(" + json.dumps(pattern) + ").phases")
    assert [row["label"] for row in rows] == ["nicht belegt"] * 4 + ["nicht modelliert"]


def test_phase_a_in_development_is_not_reported_as_a_completed_phase():
    pattern, _ = fixture()
    pattern["phase_evidence"][0]["status"] = "developing"
    rows = evaluate("wyckoffEvidence(" + json.dumps(pattern) + ").phases")
    assert rows[0]["label"] == "in Entwicklung, noch kein ST"


@pytest.mark.parametrize("field", ["scanner", "strategy", "Strategy", "canonicalStrategy", "pattern_type"])
def test_new_wyckoff_row_gets_daily_chart_without_overriding_later_user_choice(field):
    row = {field: "Wyckoff Accumulation"}
    assert evaluate("isWyckoffScannerSelection(" + json.dumps(row) + ")") is True
    assert "isCupScannerSelection(scannerData) || isWyckoffScannerSelection(scannerData) ? '1D' : '4H'" in SOURCE
    assert "patterns: isWyckoffScannerSelection(scannerData)" in SOURCE
    start = SOURCE.index("// Default only for a newly selected Cup row")
    effect = SOURCE[start:SOURCE.index("const TIMEFRAMES", start)]
    assert "}, [scannerData]);" in effect
    assert "[scannerData, timeframe]" not in effect


@pytest.mark.parametrize("bad", [None, True, "PRIVATE", "invalid-date"])
def test_unknown_dates_are_not_rendered_as_epoch_or_sensitive_raw_text(bad):
    assert evaluate("wyckoffDate(" + json.dumps(bad) + ")") == "nicht belegt"
