"""Execute v3 chart projection only: no provider, scan, signal delivery or trade."""
import copy
import json

import pytest

from test_frontend_wyckoff_evidence import PURE, SOURCE, evaluate, fixture, project
from test_frontend_scanner_lifecycle import node_run


def fixture_v3():
    pattern, candles = fixture()
    pattern.update(model="causal_wyckoff_v3", structure_id="structure:LONG:2026-01-01",
                   structure_type="Accumulation", structure_state="continuation", entry_state="target_passed",
                   phase="E", parent_structure_id=None)
    for index, event in enumerate(pattern["event_evidence"]):
        event["event_id"] = f"event:{event['name']}:{index}"
    template = pattern["event_evidence"][-1]
    for index, name in enumerate(["PS", "RangeOrigin", "Ctest", "InRangeSOS", "UpperTest", "LowerTest",
                                  "SOS", "LPS", "Backup", "LPS", "EFollowThrough"]):
        event = dict(template, name=name, event_id=f"event:{name}:{index}",
                     phase="E" if name == "EFollowThrough" else "D", price=100)
        pattern["event_evidence"].append(event)
    pattern["phase_evidence"] = [dict(phase=phase, status="inferred", start_time=candles[index]["time"],
                                    end_time=candles[index + 2]["time"], confirmed_time=candles[index + 1]["time"],
                                    confirmed_at=f"2026-01-{index + 2:02d}T21:00:00Z")
                                 for index, phase in enumerate("ABCDE")]
    def swing(number, first, last, degree, status="confirmed"):
        return dict(swing_id=f"swing:{number}", start_time=candles[first]["time"], end_time=candles[last]["time"],
                    confirmation_time=candles[min(last + 1, len(candles) - 1)]["time"],
                    start_at=f"2026-01-{first + 1:02d}T21:00:00Z", end_at=f"2026-01-{last + 1:02d}T21:00:00Z",
                    confirmed_at=f"2026-01-{last + 2:02d}T21:00:00Z", start_price=90, end_price=110,
                    direction="UP", distance=20, duration_bars=last - first, progress_atr=3.5,
                    cumulative_volume=6000, volume_per_bar=2000, degree=degree, status=status)
    pattern["swings"] = [swing(1, 0, 2, "major"), swing(2, 2, 5, "internal")]
    pattern["provisional_swing"] = swing(3, 8, 10, "internal", "unconfirmed")
    pattern["entry_trigger"] = dict(trigger_id="trigger:retest:2026-01-09", trigger_mode="confirmed_retest",
                                    confirmed_at=template["confirmed_at"],
                                    event_ids=dict(zip(["origin", "reaction", "test", "breakout", "retest"],
                                                       [event["event_id"] for event in pattern["event_evidence"][:5]])))
    return pattern, candles


def expr(function, value):
    return evaluate(function + "(" + json.dumps(value) + ")")


@pytest.mark.parametrize("entry_state", ["expired", "target_passed", "stopped", "ambiguous", "invalid_geometry", "no_trigger"])
@pytest.mark.parametrize("direction,color", [("LONG", "#10b981"), ("SHORT", "#ef4444")])
def test_failed_entry_does_not_invalidate_structure_or_historical_phases(entry_state, direction, color):
    pattern, candles = fixture_v3()
    pattern.update(entry_state=entry_state, direction=direction, signal_state="invalidated", trade_ready=False)
    assert {point["color"] for point in project(pattern, candles)["points"]} == {color}
    assert all(row["label"] == "modellbasiert abgeleitet" for row in expr("wyckoffEvidence", pattern)["phases"])
    label = expr("chartPatternLabel", pattern)
    assert "Struktur Fortsetzung" in label and "Chartkontext; kein Handelssignal" in label
    assert expr("wyckoffStates", pattern)["ready"] is False


@pytest.mark.parametrize("state,color", [("developing", "#d97706"), ("failed", "#94a3b8"), ("unclear", "#94a3b8")])
def test_structure_state_controls_structure_color_and_never_promotes_entry(state, color):
    pattern, _ = fixture_v3()
    pattern.update(structure_state=state, entry_state="ready", trade_ready=True)
    assert expr("wyckoffPatternColor", pattern) == color
    assert expr("wyckoffStates", pattern)["ready"] is False


def test_same_bar_stop_and_target_remains_unknown_without_invented_entry_or_pnl():
    pattern, _ = fixture_v3()
    pattern.update(entry_state="ambiguous", invalidation_reason="ambiguous_no_intrabar_order", trade_ready=False)
    states = expr("wyckoffStates", pattern)
    assert states["entry"] == "Intrabar-Reihenfolge unklar; kein Einstieg"
    assert states["ready"] is False and states["structure"] == "Fortsetzung"
    assert "Intrabar-Reihenfolge unklar; kein Einstieg" in expr("chartPatternLabel", pattern)


def test_ready_label_requires_current_version_and_referenced_evidence():
    pattern, _ = fixture_v3()
    pattern.update(entry_state="ready", trade_ready=True)
    assert expr("wyckoffStates", pattern)["ready"] is True
    assert "Handelsfreigabe separat" in expr("chartPatternLabel", pattern)
    for field in ["trigger_id", "confirmed_at", "event_ids"]:
        missing = copy.deepcopy(pattern)
        missing["entry_trigger"].pop(field)
        assert expr("wyckoffStates", missing)["ready"] is False
    pattern["entry_trigger"]["event_ids"]["retest"] = "nonexistent"
    assert expr("wyckoffStates", pattern)["ready"] is False
    for model in ["causal_wyckoff_v1", "causal_wyckoff_v2"]:
        pattern["model"] = model
        assert expr("wyckoffStates", pattern)["ready"] is False
        assert "Altmodell: historischer Kontext; kein Handelssignal" in expr("chartPatternLabel", pattern)


def test_v3_keeps_all_evidenced_event_types_repeated_tests_and_phase_e():
    pattern, candles = fixture_v3()
    evidence = expr("wyckoffEvidence", pattern)
    assert len(evidence["events"]) == len(pattern["event_evidence"])
    assert [event["label"] for event in evidence["events"] if event["name"] == "ST"] == ["ST #1", "ST #2"]
    assert [event["label"] for event in evidence["events"] if event["name"] == "LPS"] == ["LPS #1", "LPS #2"]
    assert next(event for event in evidence["events"] if event["name"] == "EFollowThrough")["phaseLabel"] == "E"
    assert len(project(pattern, candles)["points"]) == len(evidence["events"])


def test_phase_e_is_neither_fabricated_nor_hidden_and_markers_use_confirmation():
    pattern, candles = fixture_v3()
    projection = project(pattern, candles)
    assert [row["phase"] for row in projection["phaseSegments"]] == list("ABCDE")
    marker = next(marker for marker in projection["markers"] if marker["text"] == "E")
    proof = pattern["phase_evidence"][-1]
    assert marker["time"] == proof["confirmed_time"] != proof["start_time"]
    assert projection["phaseSegments"][-1]["data"][0]["time"] == proof["start_time"]
    pattern["phase_evidence"].pop()
    assert expr("wyckoffEvidence", pattern)["phases"][-1]["label"] == "nicht belegt"
    assert len(project(pattern, candles)["phaseSegments"]) == 4


def test_unmatched_or_future_phase_confirmation_is_not_drawn():
    pattern, candles = fixture_v3()
    pattern["phase_evidence"][-1]["confirmed_time"] = candles[-1]["time"] + 86400
    assert len(project(pattern, candles)["phaseSegments"]) == 4


def test_phase_first_confirmed_on_last_candle_keeps_marker_without_duplicate_line_times():
    pattern, candles = fixture_v3()
    proof = pattern["phase_evidence"][-1]
    proof.update(start_time=candles[-1]["time"], end_time=candles[-1]["time"], confirmed_time=candles[-1]["time"])
    projection = project(pattern, candles)
    assert len(projection["phaseSegments"][-1]["data"]) == 1
    assert any(marker["text"] == "E" for marker in projection["markers"])


def test_confirmed_and_pending_swings_remain_separate_with_original_metrics():
    pattern, candles = fixture_v3()
    evidence = expr("wyckoffEvidence", pattern)
    assert len(evidence["swings"]) == 2
    assert evidence["pending"]["status"] == "unconfirmed"
    for key in ["distance", "duration_bars", "progress_atr", "cumulative_volume", "volume_per_bar"]:
        assert evidence["swings"][1][key] == pattern["swings"][1][key]
    projection = project(pattern, candles)
    assert len(projection["swingLines"]) == 3
    assert projection["swingLines"][-1]["color"] == "#94a3b8"
    assert all("swing" not in marker["text"].lower() for marker in projection["markers"])


@pytest.mark.parametrize("invalid", ["same_time", "future_confirmation", "outside_bar", "duplicate_bar"])
def test_swing_lines_require_exact_distinct_candle_evidence(invalid):
    pattern, candles = fixture_v3()
    swing = pattern["swings"][0]
    if invalid == "same_time": swing["end_time"] = swing["start_time"]
    elif invalid == "future_confirmation": swing["confirmation_time"] = candles[-1]["time"] + 86400
    elif invalid == "outside_bar": swing["end_price"] = 1000
    else: candles.insert(0, copy.deepcopy(candles[0]))
    assert swing["swing_id"] not in {line["swing_id"] for line in project(pattern, candles)["swingLines"]}


def test_major_default_and_opt_in_internal_swing_renderer_do_not_join_events():
    pattern, candles = fixture_v3()
    script = PURE + "\nconst assert=require('assert');"
    script += "const pattern=" + json.dumps(pattern) + ";const candles=" + json.dumps(candles) + ";"
    script += """
    for (const showInternalSwings of [false, true]) {
      const calls=[],markers=[],series=[];
      renderWyckoffChartEvidence({pattern,candles,timeframe:'1D',series,markers,showInternalSwings,
        LineStyle:{Solid:0,Dashed:2},chart:{addLineSeries(options){const call={options};calls.push(call);return{setData(data){call.data=data;}};}}});
      const swings=calls.filter(call=>call.options.lineVisible!==false&&!call.options.title&&call.options.lineWidth<=2);
      assert.equal(swings.length,showInternalSwings?3:1);
      assert(calls.filter(call=>call.options.lineWidth===3).every(call=>!call.options.title));
      assert(calls.filter(call=>call.options.title).every(call=>call.options.title.startsWith('Wyckoff ')));
      for(const call of calls.filter(call=>call.options.lineVisible===false))assert.equal(call.data.length,1);
      if(showInternalSwings)assert.equal(swings.at(-1).options.lineStyle,2);
    }
    """
    node_run(script)


def test_no_v3_geometry_is_transferred_to_another_timeframe():
    pattern, candles = fixture_v3()
    assert all(not value for value in project(pattern, candles, timeframe="1H").values())


def test_structure_parent_and_child_are_retained_as_distinct_chart_rows():
    pattern, _ = fixture_v3()
    child = copy.deepcopy(pattern)
    child.update(structure_id="child-range", parent_structure_id=pattern["structure_id"], phase="B",
                 structure_type="Reaccumulation", structure_state="developing", entry_state="no_trigger")
    rows = evaluate("wyckoffDisplayRows(" + json.dumps([pattern, child]) + ",null,'LONG')")
    assert len(rows) == 2
    assert rows[0]["pattern"]["phase"] == "E" and rows[1]["pattern"]["phase"] == "B"
    assert rows[1]["pattern"]["parent_structure_id"] == rows[0]["pattern"]["structure_id"]


def test_both_chart_consumers_offer_compact_evidence_and_internal_swing_control():
    assert SOURCE.count("showInternalSwings: overlays.wyckoffSwings") == 2
    assert SOURCE.count("onToggleInternalSwings={() => setOverlays") == 2
    assert 'aria-label="Wyckoff-Swingevidenz"' in SOURCE
    assert "unbestaetigt; kein Ereignis/Trigger" in SOURCE
    assert "parent_structure_id" in SOURCE and "history_context?.status" in SOURCE
    assert "row.phase !== 'E'" not in SOURCE
    assert "p.trade_ready !== true || p.signal_state === 'invalidated'" not in SOURCE
    assert "keine automatische Staerkerangliste" in SOURCE
    assert "Chartkontext ist kein Scanner-Treffer, Tracking-Einstieg oder Signal-Mail" in SOURCE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("kind", ["climactic", "nonclimactic", "continuation", "phase_e"])
def test_real_engine_evidence_projects_phases_swings_and_trigger_without_schema_translation(direction, kind):
    from test_wyckoff_engine import analyze, selected, textbook_bars
    from test_wyckoff_robustness import mirror_bar_update
    from test_wyckoff_v3_engine import range_bars

    bars = (textbook_bars(direction) if kind in {"climactic", "phase_e"}
            else range_bars(direction, continuation=kind == "continuation"))
    if kind == "phase_e":
        mirror_bar_update(bars, 92, direction, open=110., high=112., low=109., close=111., volume=1300.)
        mirror_bar_update(bars, 93, direction, open=111., high=113., low=110., close=112., volume=1200.)
        bars = bars[:94]
    pattern = selected(analyze(bars, direction, count=len(bars)), direction)
    candles = [dict(time=int(bar["open_time"].timestamp()), **{key: bar[key] for key in ("open", "high", "low", "close")})
               for bar in bars]
    assert pattern["model"] == "causal_wyckoff_v3"
    projection = project(pattern, candles)
    assert len(projection["points"]) == len(pattern["events"])
    assert {segment["phase"] for segment in projection["phaseSegments"]} == {proof["phase"] for proof in pattern["phase_evidence"]}
    assert {segment["swing_id"] for segment in projection["swingLines"] if segment["status"] == "confirmed"} == {swing["swing_id"] for swing in pattern["swings"]}
    assert any(segment["status"] == "unconfirmed" for segment in projection["swingLines"])
    assert expr("wyckoffStates", pattern)["ready"] is True
    evidence = expr("wyckoffEvidence", pattern)
    assert all(row["label"] == "modellbasiert abgeleitet" for row in evidence["phases"] if row.get("proof"))
    if kind == "phase_e":
        assert pattern["phase"] == "E" and any(marker["text"] == "E" for marker in projection["markers"])


def test_chart_event_codes_are_compact_while_evidence_keeps_full_names():
    pattern, candles = fixture_v3()
    projection = project(pattern, candles)
    labels = ' '.join(marker["text"] for marker in projection["markers"])
    assert 'EFollowThrough' not in labels and 'Backup' not in labels and 'RangeOrigin' not in labels
    assert ' BK' in labels and ' C-Test' in labels and 'LPS#2' in labels
    assert all(marker["text"] in 'ABCDE' for marker in projection["markers"] if marker["position"] == 'belowBar')
    evidence = expr("wyckoffEvidence", pattern)
    assert {'EFollowThrough', 'Backup', 'RangeOrigin'} <= {event["name"] for event in evidence["events"]}


def test_narrow_plot_uses_only_event_ordinals_but_preserves_phase_markers_and_desktop_codes():
    pattern, candles = fixture_v3()
    script = PURE + "\nconst assert=require('assert');"
    script += "const pattern=" + json.dumps(pattern) + ";const candles=" + json.dumps(candles) + ";"
    script += """
    for (const width of [390, 479, 480, 920, null]) {
      const markers=[];
      const chart={addLineSeries(){return{setData(){}};}};
      if(width!==null)chart.timeScale=()=>({width:()=>width});
      renderWyckoffChartEvidence({pattern,candles,timeframe:'1D',series:[],markers,
        LineStyle:{Solid:0,Dashed:2},chart});
      const eventMarkers=markers.filter(marker=>marker.position==='aboveBar');
      if(width!==null&&width<480){
        assert(eventMarkers.every(marker=>/^\\d+(\\/\\d+)*$/.test(marker.text)));
        assert(eventMarkers.some(marker=>marker.text.includes('/')));
      }else assert(eventMarkers.some(marker=>marker.text.includes('C-Test')));
      assert.deepEqual(markers.filter(marker=>marker.position==='belowBar').map(marker=>marker.text),['A','B','C','D','E']);
    }
    """
    node_run(script)
