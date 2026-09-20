"""The chosen Cup geometry is descriptive, dated, precise and safely persisted."""
from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

import api
from modules.cup_pattern_evidence import project_cup_geometry_evidence, unavailable
from test_cup_handle_audit_fixes import _cup_handle_bars, _mk_candidate, _mock_session


def _dated_bars(*, scale=1.):
    bars = _cup_handle_bars()
    start = date(2026, 4, 1)
    for i, bar in enumerate(bars):
        bar["date"] = (start + timedelta(days=i)).isoformat()
        for field in ("open", "high", "low", "close"):
            bar[field] *= scale
    return bars


def _setup(*, scale=1.):
    return api._detect_cup_handle_breakout(_dated_bars(scale=scale), current_price=101.7 * scale)


def test_detector_anchors_describe_exact_selected_window_with_original_precision():
    scale = 1.0000001234567
    bars = _dated_bars(scale=scale)
    setup = api._detect_cup_handle_breakout(bars, current_price=101.7 * scale)
    assert setup is not None
    evidence = setup["cup_pattern_evidence"]
    assert evidence["version"] == "cup_geometry_v1"
    assert evidence["status"] == "available"
    assert evidence["timeframe"] == "1D"
    assert evidence["as_of_semantics"] == "selected_input_last_session"
    assert evidence["index_basis"] == "selected_window_zero_based"
    selected = bars[-(setup["cup_length"] + setup["handle_length"]):]
    assert evidence["cup_length"] == setup["cup_length"]
    assert evidence["handle_length"] == setup["handle_length"]
    assert evidence["window_start_session"] == selected[0]["date"]
    assert evidence["as_of_session"] == evidence["window_end_session"] == selected[-1]["date"]
    anchors = evidence["anchors"]
    for anchor in anchors.values():
        original = selected[anchor["index"]]
        assert anchor["session"] == original["date"]
        assert anchor["price"] == original[anchor["price_field"]]
    assert anchors["left_rim"]["price"] != setup["cup_lip"]  # Original, not display-rounded.
    assert anchors["handle_start"]["index"] == setup["cup_length"]
    assert anchors["handle_end"]["index"] == len(selected) - 2
    assert anchors["breakout"]["index"] == len(selected) - 1
    assert anchors["handle_end"]["session"] < anchors["breakout"]["session"]
    assert evidence["handle_includes_breakout"] is True
    assert project_cup_geometry_evidence(evidence) == evidence


@pytest.mark.parametrize("scale", [.1, 1., 17.1234567])
def test_dates_and_descriptive_geometry_never_change_signal_math(scale):
    dated = _dated_bars(scale=scale)
    undated = [{key: value for key, value in row.items() if key != "date"} for row in dated]
    left = api._detect_cup_handle_breakout(dated, current_price=101.7 * scale)
    right = api._detect_cup_handle_breakout(undated, current_price=101.7 * scale)
    assert left is not None and right is not None
    assert left.pop("cup_pattern_evidence")["status"] == "available"
    assert right.pop("cup_pattern_evidence") == unavailable("missing_session")
    assert left == right


def test_evidence_failure_does_not_change_detector_scalar_result(monkeypatch):
    original = _setup()
    monkeypatch.setattr(api, "_build_cup_geometry_evidence", lambda *a, **kw: unavailable("invalid_session"))
    failed = _setup()
    assert original.pop("cup_pattern_evidence")["status"] == "available"
    assert failed.pop("cup_pattern_evidence")["status"] == "unavailable"
    assert original == failed


def test_filter_and_bounded_watch_keep_geometry_and_unchanged_trade_fields(monkeypatch):
    _mock_session(monkeypatch, allowed=False)
    monkeypatch.setattr(api, "_current_us_market_date_str", lambda: "2026-09-18")
    original = api._detect_cup_handle_breakout
    candidate = _mk_candidate(bars=_dated_bars())
    row = api._apply_cup_handle_strategy_filter(deepcopy(candidate), {"min_dollar_volume": 2_000_000})
    assert row is not None
    evidence = row["cup_pattern_evidence"]
    assert evidence["status"] == "available" and evidence["symbol"] == "CUPX"
    assert evidence["anchors"] == original(candidate["_daily_bars"], current_price=101.7)["cup_pattern_evidence"]["anchors"]
    watched = api._cup_handle_watch_row(row)
    assert watched["cup_pattern_evidence"] == evidence
    assert watched["cup_pattern_evidence"] is not evidence
    assert watched["cup_pattern_evidence"]["anchors"] is not evidence["anchors"]

    def legacy_detector(*args, **kwargs):
        result = original(*args, **kwargs)
        result.pop("cup_pattern_evidence", None)
        return result

    monkeypatch.setattr(api, "_detect_cup_handle_breakout", legacy_detector)
    legacy_row = api._apply_cup_handle_strategy_filter(deepcopy(candidate), {"min_dollar_volume": 2_000_000})
    assert legacy_row is not None
    assert legacy_row.pop("cup_pattern_evidence") == unavailable("legacy_evidence_missing", symbol="CUPX")
    row.pop("cup_pattern_evidence")
    assert legacy_row == row


def test_legacy_watch_rows_never_get_invented_geometry():
    row = {"ticker": "CUPX", "Entry": 101.2, "Handle_Low": 92.872}
    assert "cup_pattern_evidence" not in api._cup_handle_watch_row(row)


def test_projection_drops_arbitrary_nested_values_and_rebinds_only_matching_symbol():
    evidence = _setup()["cup_pattern_evidence"]
    noisy = deepcopy(evidence)
    noisy.update(symbol="CUPX", injected="private URL", arbitrary={"secret": "not allowed"})
    noisy["anchors"]["breakout"]["extra"] = {"private": "do not persist"}
    noisy["anchors"]["fake"] = {"date": "2026-01-01", "price": 1}
    row = {"ticker": "CUPX", "cup_pattern_evidence": noisy, "private": "do not persist"}
    clean = api._cup_handle_watch_row(row)
    assert clean["cup_pattern_evidence"] == dict(evidence, symbol="CUPX")
    assert "private" not in json.dumps(clean)
    assert project_cup_geometry_evidence(noisy, symbol="OTHER") == unavailable("symbol_mismatch", symbol="OTHER")


@pytest.mark.parametrize("key,value", [
    ("price", True), ("price", float("nan")), ("price", float("inf")), ("price", 2**2048),
    ("price", "101.70"), ("session", "2026-99-99"), ("index", True),
    ("index", 99999), ("price_field", "open"),
])
def test_projection_rejects_malformed_anchor_without_fabricating_replacement(key, value):
    evidence = _setup()["cup_pattern_evidence"]
    evidence["anchors"]["breakout"][key] = value
    result = project_cup_geometry_evidence(evidence, symbol="CUPX")
    assert result == unavailable("invalid_evidence", symbol="CUPX")
    assert "anchors" not in result


@pytest.mark.parametrize("key,value", [
    ("version", "future_model"), ("timeframe", "4H"),
    ("cup_length", 1), ("handle_length", 0), ("as_of_session", "2026-12-31"),
    ("as_of_semantics", "live_scan_time"), ("index_basis", "global_chart_array"),
    ("handle_includes_breakout", False),
])
def test_projection_rejects_inconsistent_window_contract(key, value):
    evidence = _setup()["cup_pattern_evidence"]
    evidence[key] = value
    assert project_cup_geometry_evidence(evidence)["status"] == "unavailable"


def test_projection_recomputes_chronology_flags_not_arbitrary_text():
    evidence = _setup()["cup_pattern_evidence"]
    original = deepcopy(evidence)
    evidence["geometry_status"] = "invented shape"
    evidence["geometry_issues"] = ["freeform private text"]
    assert project_cup_geometry_evidence(evidence) == original


def test_unavailable_reason_cannot_copy_arbitrary_nested_data_or_raise():
    malformed = {"version": "cup_geometry_v1", "timeframe": "1D", "status": "unavailable",
                 "reason": {"private": "not copied"}}
    assert project_cup_geometry_evidence(malformed) == unavailable("invalid_evidence")
