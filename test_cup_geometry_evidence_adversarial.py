"""Independent checks: describe selected Cup extrema, never idealize them."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

import api
from modules.cup_pattern_evidence import build_cup_geometry_evidence, project_cup_geometry_evidence
from test_cup_handle_scanner import _bar, _cup_handle_bars


def _dated(bars):
    result = deepcopy(bars)
    session = date(2026, 1, 5)
    for bar in result:
        while session.weekday() >= 5:
            session += timedelta(days=1)
        bar["date"] = session.isoformat()
        session += timedelta(days=1)
    return result


def _describe_selected(bars, setup):
    segment = bars[-(setup["cup_length"] + setup["handle_length"]):]
    evidence = build_cup_geometry_evidence(
        segment, cup_length=setup["cup_length"], handle_length=setup["handle_length"],
        session_getter=api._daily_bar_date_str, number_getter=api._bar_num,
    )
    return segment, evidence


def test_extrema_ties_use_first_real_bar_in_each_selected_region():
    bars = _dated(_cup_handle_bars())
    bars[1]["high"] = bars[0]["high"]
    bars[42]["low"] = bars[41]["low"]
    bars[88]["high"] = bars[89]["high"]
    bars[94]["low"] = bars[93]["low"]
    setup = api._detect_cup_handle_breakout(bars, current_price=101.7)
    assert setup is not None
    segment, expected = _describe_selected(bars, setup)
    assert setup["cup_pattern_evidence"] == expected
    assert expected["status"] == "available"
    count = setup["cup_length"]
    regions = {"left_rim": (range(max(8, int(count * .32))), "high", max),
               "bottom": (range(int(count * .22), int(count * .78)), "low", min),
               "right_rim": (range(int(count * .62), count), "high", max),
               "handle_low": (range(count, len(segment)), "low", min)}
    for name, (indices, field, extremum) in regions.items():
        extreme_price = extremum(segment[i][field] for i in indices)
        tied_indices = [i for i in indices if segment[i][field] == extreme_price]
        assert len(tied_indices) >= 2
        anchor = expected["anchors"][name]
        assert anchor == {"index": tied_indices[0], "session": segment[tied_indices[0]]["date"],
                          "price": extreme_price, "price_field": field}


def test_handle_low_on_breakout_keeps_distinct_low_and_close_without_relabeling():
    bars = _dated(_cup_handle_bars())
    bars[-1]["low"] = 92.8
    setup = api._detect_cup_handle_breakout(bars, current_price=101.7)
    assert setup is not None
    evidence = setup["cup_pattern_evidence"]
    anchors = evidence["anchors"]
    assert evidence["handle_includes_breakout"] is True
    assert evidence["handle_low_on_breakout"] is True
    assert evidence["geometry_status"] == "chronology_mismatch"
    assert "handle_low_on_breakout" in evidence["geometry_issues"]
    assert anchors["handle_low"]["session"] == anchors["breakout"]["session"] == bars[-1]["date"]
    assert anchors["handle_low"]["index"] == anchors["breakout"]["index"]
    assert anchors["handle_low"]["price"] == 92.8
    assert anchors["breakout"]["price"] == 101.7
    assert anchors["handle_end"]["session"] == bars[-2]["date"]
    projected = project_cup_geometry_evidence(evidence, symbol="EXPD")
    assert projected["anchors"] == anchors
    assert projected["geometry_issues"] == evidence["geometry_issues"]


def test_overlapping_regions_report_selected_chronology_mismatch_without_new_pattern_search():
    bars = _dated(_cup_handle_bars())
    bars[61].update(_bar(100, high=101.2, low=99, volume=1_150_000))
    bars[65].update(_bar(71, high=72, low=70, volume=1_150_000))
    setup = api._detect_cup_handle_breakout(bars, current_price=101.7)
    assert setup is not None  # Descriptive evidence is not a new admission gate.
    evidence = setup["cup_pattern_evidence"]
    anchors = evidence["anchors"]
    assert anchors["right_rim"]["index"] < anchors["bottom"]["index"]
    assert evidence["geometry_status"] == "chronology_mismatch"
    assert "bottom_not_before_right_rim" in evidence["geometry_issues"]
    assert evidence == _describe_selected(bars, setup)[1]
    projected = project_cup_geometry_evidence(evidence, symbol="EXPD")
    assert projected["anchors"] == anchors
    assert projected["geometry_status"] == "chronology_mismatch"


@pytest.mark.parametrize("case,reason", [
    ("missing", "missing_session"), ("impossible", "invalid_session"),
    ("duplicate", "non_monotonic_sessions"), ("reverse", "non_monotonic_sessions"),
])
def test_date_defects_make_only_evidence_unavailable_not_silently_repaired(case, reason):
    bars = _dated(_cup_handle_bars())
    baseline = api._detect_cup_handle_breakout(bars, current_price=101.7)
    if case == "missing":
        bars[41].pop("date")
    elif case == "impossible":
        bars[41]["date"] = "2026-02-30"
    elif case == "duplicate":
        bars[41]["date"] = bars[40]["date"]
    else:
        bars[40]["date"], bars[41]["date"] = bars[41]["date"], bars[40]["date"]
    result = api._detect_cup_handle_breakout(bars, current_price=101.7)
    assert result is not None
    assert {k: v for k, v in result.items() if k != "cup_pattern_evidence"} == {
        k: v for k, v in baseline.items() if k != "cup_pattern_evidence"}
    evidence = result["cup_pattern_evidence"]
    assert evidence["status"] == "unavailable" and evidence["reason"] == reason
    assert "anchors" not in evidence and "as_of_session" not in evidence


def test_projection_recomputes_warning_instead_of_trusting_forged_ordered_label():
    bars = _dated(_cup_handle_bars())
    bars[-1]["low"] = 92.8
    evidence = api._detect_cup_handle_breakout(bars, current_price=101.7)["cup_pattern_evidence"]
    evidence.update(geometry_status="ordered", geometry_issues=[], handle_low_on_breakout=False)
    evidence["secret"] = {"not_public": "must not be copied"}
    result = project_cup_geometry_evidence(evidence, symbol="EXPD")
    assert result["geometry_status"] == "chronology_mismatch"
    assert result["geometry_issues"] == ["handle_low_on_breakout"]
    assert result["handle_low_on_breakout"] is True
    assert "secret" not in result


def test_projection_giant_integer_price_is_unavailable_not_an_annotation_crash():
    bars = _dated(_cup_handle_bars())
    evidence = api._detect_cup_handle_breakout(bars, current_price=101.7)["cup_pattern_evidence"]
    evidence["anchors"]["bottom"]["price"] = 10 ** 999
    result = project_cup_geometry_evidence(evidence, symbol="EXPD")
    assert result["status"] == "unavailable"
    assert result["reason"] == "invalid_evidence"
    assert "anchors" not in result


def test_builder_giant_integer_price_is_unavailable_without_rejecting_pattern():
    bars = _dated(_cup_handle_bars())
    setup = api._detect_cup_handle_breakout(bars, current_price=101.7)
    segment, _ = _describe_selected(bars, setup)
    segment[0]["high"] = 10 ** 999
    result = build_cup_geometry_evidence(
        segment, cup_length=setup["cup_length"], handle_length=setup["handle_length"],
        session_getter=lambda bar: bar["date"], number_getter=lambda bar, field, alias: bar[field],
    )
    assert result["status"] == "unavailable"
    assert result["reason"] == "invalid_bar_values"
    assert "anchors" not in result
