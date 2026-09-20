"""Execute the shipped Cup chart projection without browser/provider access."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_frontend_scanner_lifecycle import node_run


SOURCE = (Path(__file__).parent / "frontend/index.html").read_text(encoding="utf-8")


def evaluate(expression):
    start = SOURCE.index("function isCupScannerSelection(")
    end = SOURCE.index("function CupPatternEvidence(", start)
    return json.loads(node_run(SOURCE[start:end] + "\nconsole.log(JSON.stringify(" + expression + "));"))


def fixture(extra_bars=0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [{"time": int((start + timedelta(days=i)).timestamp()),
                "open": 100, "high": 105, "low": 95, "close": 101} for i in range(100 + extra_bars)]
    definitions = [("left_rim", 5, "high"), ("bottom", 40, "low"), ("right_rim", 75, "high"),
                   ("handle_start", 80, "close"), ("handle_low", 90, "low"),
                   ("handle_end", 98, "close"), ("breakout", 99, "close")]
    session = lambda i: (start + timedelta(days=i)).date().isoformat()
    evidence = {"version": "cup_geometry_v1", "status": "available", "symbol": "TEST", "timeframe": "1D",
                "as_of_semantics": "selected_input_last_session", "as_of_session": session(99),
                "window_start_session": session(0), "window_end_session": session(99),
                "cup_length": 80, "handle_length": 20, "index_basis": "selected_window_zero_based",
                "handle_includes_breakout": True, "handle_low_on_breakout": False,
                "geometry_status": "ordered", "geometry_issues": [],
                "anchors": {name: {"session": session(i), "index": i, "price_field": field,
                                   "price": candles[i][field]} for name, i, field in definitions}}
    return {"ticker": "TEST", "timeframe": "1D", "row": {"ticker": "TEST", "scanner": "Cup and Handle Breakout",
            "cup_pattern_evidence": evidence}, "chartData": {"ticker": "TEST", "timeframe": "1D", "candles": candles}}


def project(payload):
    return evaluate("projectCupChartEvidence(" + json.dumps(payload) + ")")


def test_all_seven_exact_anchors_and_bounded_rim_are_projected():
    result = project(fixture())
    assert result["status"] == "available"
    assert len(result["anchors"]) == 7
    assert result["historical"] is False
    assert result["canConnect"] is True
    assert [p["key"] for p in result["anchors"]] == ["left_rim", "bottom", "right_rim", "handle_start", "handle_low", "handle_end", "breakout"]
    assert result["rim"] == [{"time": fixture()["chartData"]["candles"][i]["time"], "value": 105} for i in (5, 75)]
    assert result["initialRange"]["from"] <= 5 and result["initialRange"]["to"] >= 99
    assert [marker["text"] for marker in result["markers"]] == list("1234567")


def test_missing_legacy_evidence_is_unknown_not_no_cup():
    payload = fixture()
    del payload["row"]["cup_pattern_evidence"]
    result = project(payload)
    assert result["status"] == "unavailable"
    assert result["anchors"] == []
    assert "unbekannt" in result["message"].lower()


def test_regular_stock_is_unchanged_and_keeps_default_zoom():
    payload = fixture()
    payload["row"] = {"ticker": "TEST", "scanner": "Momentum Breakout Long"}
    assert project(payload)["status"] == "not_applicable"
    assert evaluate("cupInitialVisibleRange(200, null)") == {"from": 120, "to": 199}


@pytest.mark.parametrize("field,bad", [("symbol", "OTHER"), ("version", "future_v2"), ("timeframe", "4H"),
    ("as_of_session", "2026-02-30"), ("index_basis", "chart_index"), ("as_of_semantics", "now"),
    ("cup_length", True), ("handle_length", -1), ("handle_includes_breakout", False)])
def test_invalid_evidence_never_guesses_coordinates(field, bad):
    payload = fixture()
    payload["row"]["cup_pattern_evidence"][field] = bad
    assert project(payload)["anchors"] == []


@pytest.mark.parametrize("where,key,value", [("chartData", "ticker", "OTHER"), ("chartData", "timeframe", "4H"),
                                           ("row", "ticker", "OTHER")])
def test_ticker_and_timeframe_identity_cannot_leak_anchors(where, key, value):
    payload = fixture()
    payload[where][key] = value
    assert project(payload)["anchors"] == []


def test_user_timeframe_change_requires_explicit_daily_control():
    payload = fixture()
    payload["timeframe"] = "4H"
    result = project(payload)
    assert result["dailyRequired"] is True
    assert result["anchors"] == []


@pytest.mark.parametrize("bad", [None, True, -1, "105", 1e99])
def test_bad_price_is_not_rendered(bad):
    payload = fixture()
    payload["row"]["cup_pattern_evidence"]["anchors"]["left_rim"]["price"] = bad
    assert project(payload)["anchors"] == []


def test_split_or_different_price_basis_is_visible_unknown():
    payload = fixture()
    payload["chartData"]["candles"][5]["high"] = 52.5
    result = project(payload)
    assert result["anchors"] == []
    assert "Preis" in result["message"]


def test_tiny_float_rounding_is_allowed_but_cent_scale_move_is_not():
    payload = fixture()
    payload["row"]["cup_pattern_evidence"]["anchors"]["left_rim"]["price"] += 1e-7
    assert len(project(payload)["anchors"]) == 7
    payload["row"]["cup_pattern_evidence"]["anchors"]["left_rim"]["price"] += 0.01
    assert project(payload)["anchors"] == []


@pytest.mark.parametrize("change", ["missing", "duplicate", "index", "wrong_field", "outside_window"])
def test_missing_or_ambiguous_bar_mapping_is_not_interpolated(change):
    payload = fixture()
    evidence = payload["row"]["cup_pattern_evidence"]
    if change == "missing": del payload["chartData"]["candles"][5]
    elif change == "duplicate": payload["chartData"]["candles"].insert(5, copy.deepcopy(payload["chartData"]["candles"][5]))
    elif change == "index": evidence["anchors"]["left_rim"]["index"] = 6
    elif change == "wrong_field": evidence["anchors"]["left_rim"]["price_field"] = "close"
    else: evidence["anchors"]["left_rim"]["session"] = "2025-12-31"
    assert project(payload)["anchors"] == []


def test_new_chart_sessions_preserve_historical_points_not_new_confirmation():
    result = project(fixture(extra_bars=20))
    assert len(result["anchors"]) == 7
    assert result["historical"] is True
    assert "keine aktuelle Neubestaetigung" in result["message"]


def test_chart_flagged_stale_remains_historical_not_current():
    payload = fixture()
    payload["chartData"]["chart_freshness"] = {"stale": True}
    assert project(payload)["historical"] is True


def test_chronology_issue_retains_exact_legend_without_connection():
    payload = fixture()
    ev = payload["row"]["cup_pattern_evidence"]
    ev["geometry_status"] = "chronology_mismatch"
    ev["geometry_issues"] = ["left_rim_after_bottom", "PRIVATE_UNKNOWN"]
    result = project(payload)
    assert len(result["anchors"]) == 7
    assert result["canConnect"] is False and result["rim"] == []
    assert "PRIVATE" not in json.dumps(result)
    assert "Reihenfolge" in result["message"]


def test_frontend_independently_refuses_false_ordered_claim():
    payload = fixture()
    ev = payload["row"]["cup_pattern_evidence"]
    ev["anchors"]["bottom"].update(index=2, session="2026-01-03")
    result = project(payload)
    assert len(result["anchors"]) == 7
    assert result["canConnect"] is False


def test_handle_low_and_breakout_same_session_keep_distinct_exact_prices():
    payload = fixture()
    ev = payload["row"]["cup_pattern_evidence"]
    ev["handle_low_on_breakout"] = True
    ev["anchors"]["handle_low"].update(index=99, session=ev["as_of_session"])
    result = project(payload)
    anchors = {a["key"]: a for a in result["anchors"]}
    assert anchors["handle_low"]["time"] == anchors["breakout"]["time"]
    assert anchors["handle_low"]["price"] == 95 and anchors["breakout"]["price"] == 101


def test_chart_render_and_both_scanner_click_paths_preserve_evidence():
    sidebar = SOURCE[SOURCE.index("function DetailSidebar("):SOURCE.index("function App(")]
    scanner = SOURCE[SOURCE.index("function ScannerTab("):SOURCE.index("function BIScannerTab(")]
    assert scanner.count("onSelectTicker(scannerSelection(item, marketType, strategy))") == 2
    assert "Erkannte Anker, keine ideale U-Kurve" in SOURCE
    assert "1D-Daten bis" in SOURCE
    assert "lineVisible: false" in sidebar and "pointMarkersVisible: true" in sidebar
    assert "markers.push(...cupEvidence.markers)" in sidebar
    assert sidebar.count("candleSeries.current.setMarkers(markers)") == 1
    assert "cupInitialVisibleRange(totalBars, cupEvidence)" in sidebar
    assert "<CupPatternEvidence evidence={cupEvidence}" in sidebar
    assert sidebar.index("<CupPatternEvidence evidence={cupEvidence}") > sidebar.index("ref={chartContainer}")
    component = SOURCE[SOURCE.index("function CupPatternEvidence("):SOURCE.index("// DetailSidebar Component")]
    assert '<details className="mt-2">' in component
    assert '7 datierte Anker anzeigen' in component
    assert '{index + 1} · {anchor.label}' in component
    assert "minBarSpacing: cupEvidence.anchors.length ? 0.5 : 4" in sidebar
    assert "scannerData === prevOverlaysRef.current._scannerData" in sidebar
    assert "useState(() => isCupScannerSelection(scannerData) ? '1D' : '4H')" in sidebar
    selection_effect = sidebar[sidebar.index("// Default only for a newly selected Cup row"):sidebar.index("const TIMEFRAMES")]
    assert "}, [scannerData]);" in selection_effect
    assert "[scannerData, timeframe]" not in selection_effect


def test_stock_selection_retains_row_and_crypto_stays_explicit():
    assert evaluate("scannerSelection({ticker:'TEST',cup_pattern_evidence:{version:'cup_geometry_v1'}},'stocks','Cup and Handle Breakout')")["cup_pattern_evidence"] == {"version": "cup_geometry_v1"}
    assert evaluate("scannerSelection({ticker:'BTC'},'crypto','test')")["isCrypto"] is True


@pytest.mark.parametrize("cup_length,handle_length", [(80, 20), (150, 20)])
def test_real_backend_projection_roundtrips_into_all_exact_chart_points(cup_length, handle_length):
    from modules.cup_pattern_evidence import build_cup_geometry_evidence, project_cup_geometry_evidence
    payload = fixture(extra_bars=cup_length + handle_length - 100)
    candles = payload["chartData"]["candles"]
    evidence = build_cup_geometry_evidence(candles, cup_length=cup_length, handle_length=handle_length,
        session_getter=lambda bar: datetime.fromtimestamp(bar["time"], timezone.utc).date().isoformat(),
        number_getter=lambda bar, field, short: bar[field])
    payload["row"]["cup_pattern_evidence"] = project_cup_geometry_evidence(evidence, symbol="TEST")
    result = project(payload)
    assert len(result["anchors"]) == 7
    assert result["canConnect"] is True
    assert result["initialRange"] == {"from": -5, "to": len(candles) + 4}
    for anchor in result["anchors"]:
        raw = payload["row"]["cup_pattern_evidence"]["anchors"][anchor["key"]]
        assert anchor["session"] == raw["session"]
        assert anchor["price"] == raw["price"]


def test_unknown_free_text_and_private_nested_fields_never_reach_legend():
    payload = fixture()
    payload["row"]["cup_pattern_evidence"].update(reason="PRIVATE_SECRET", geometry_issues=["PRIVATE_SECRET"],
                                               arbitrary_field="PRIVATE_SECRET")
    payload["row"]["cup_pattern_evidence"]["anchors"]["left_rim"]["label"] = "PRIVATE_SECRET"
    assert "PRIVATE_SECRET" not in json.dumps(project(payload))


def test_future_or_late_anchor_outside_the_bound_window_cannot_be_drawn():
    payload = fixture(extra_bars=10)
    evidence = payload["row"]["cup_pattern_evidence"]
    evidence["anchors"]["breakout"].update(session="2026-04-12", index=101)
    assert project(payload)["anchors"] == []


def test_sidebar_chart_width_uses_actual_mobile_container_not_desktop_constant():
    assert evaluate("sidebarChartContainerWidth({clientWidth:357})") == 357
    assert evaluate("sidebarChartContainerWidth({clientWidth:299.8})") == 299
    assert evaluate("sidebarChartContainerWidth({clientWidth:0})") is None


def test_resizing_chart_preserves_the_users_current_logical_zoom():
    result = evaluate("""(() => {
        const calls=[]; const saved={from:31.5,to:70.5};
        const scale={getVisibleLogicalRange:()=>saved,setVisibleLogicalRange:r=>calls.push(['range',r])};
        const chart={timeScale:()=>scale,applyOptions:o=>calls.push(['size',o])};
        const width=resizeSidebarChart(chart,{clientWidth:357},250);
        return {width,calls};
    })()""")
    assert result == {"width": 357, "calls": [["size", {"width": 357, "height": 250}],
                                              ["range", {"from": 31.5, "to": 70.5}]]}


def test_sidebar_observes_container_and_window_resize_and_disconnects():
    sidebar = SOURCE[SOURCE.index("function DetailSidebar("):SOURCE.index("function App(")]
    assert "new window.ResizeObserver(resizeHandler)" in sidebar
    assert "resizeObserver.observe(chartContainer.current)" in sidebar
    assert "resizeObserver?.disconnect()" in sidebar
    assert "window.removeEventListener('resize', resizeHandler)" in sidebar
    assert "return 480 - 60" not in sidebar
