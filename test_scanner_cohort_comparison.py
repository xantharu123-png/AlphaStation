from copy import deepcopy

import pytest

from modules.scanner_cohort_comparison import compare_exported_cohorts, input_fingerprint


def _export():
    rows = []
    for index, gross in enumerate([2.0, -1.0, -1.0, 1.0]):
        snapshot = {"price": 100 + index, "daily_bars": [index], "available_at": f"2026-09-0{index+1}T10:00:00Z"}
        result = {"input_sha256": input_fingerprint(snapshot), "selected": True,
                  "state": "DECIDED", "entry_filled": True, "gross_r": gross,
                  "roundtrip_cost_r": 0.1, "exit_at": f"2026-09-0{index+1}T12:00:00Z"}
        rows.append({"opportunity_id": str(index), "ticker": "SYNTHETIC",
                     "observed_at": f"2026-09-0{index+1}T10:00:00Z", "input": snapshot,
                     "old": deepcopy(result), "new": deepcopy(result)})
    return {"schema_version": 1, "scanner": "stock_strategy:Momentum Breakout Long",
            "market": "US_equities", "direction": "LONG", "horizon": "swing", "timeframe": "1D", "regime": "unknown",
            "input_kind": "synthetic_fixture", "versions": {"old": "old-v1", "new": "new-v2"},
            "cost_policy": "Exported roundtrip costs divided by each trade's initial risk",
            "window": {"start": "2026-09-01T00:00:00Z", "end": "2026-09-07T00:00:00Z"},
            "opportunities": rows}


def test_same_input_net_r_and_drawdown_are_exact_and_synthetic_is_not_empirical():
    export = _export()
    export["opportunities"][1]["new"].update(selected=False, state="NOT_SELECTED")
    result = compare_exported_cohorts(export)
    assert result["same_input_verified"] is True
    assert result["old"]["total_net_r"] == pytest.approx(0.6)
    assert result["old"]["max_drawdown_r"] == pytest.approx(2.2)
    assert result["new"]["total_net_r"] == pytest.approx(1.7)
    assert result["selection_removed"] == ["1"]
    assert result["paired_decided"] == 3
    assert result["paired_mean_net_r_delta"] == 0
    assert result["empirical_performance_available"] is False
    assert result["live_validation_eligible"] is False
    assert result["old"]["profit_factor"] == pytest.approx(2.8 / 2.2)
    assert result["old"]["max_consecutive_losses"] == 2
    assert result["old"]["win_rate_wilson_95"]["lower"] < 0.5 < result["old"]["win_rate_wilson_95"]["upper"]


def test_missing_open_and_unfilled_results_are_not_zero_returns_or_wins():
    export = _export()
    export["opportunities"][0]["new"].update(state="NO_FILL", entry_filled=False)
    export["opportunities"][1]["new"]["state"] = "UNRESOLVED"
    export["opportunities"][2]["new"]["roundtrip_cost_r"] = None
    export["opportunities"][3]["new"]["gross_r"] = None
    result = compare_exported_cohorts(export)
    assert result["new"]["decided"] == 0
    assert result["new"]["no_fill"] == 1
    assert result["new"]["unresolved"] == 1
    assert result["new"]["missing"] == 2
    assert result["new"]["total_net_r"] is None
    assert result["new"]["win_rate"] is None
    assert result["new"]["max_drawdown_r"] is None
    assert result["paired_mean_net_r_delta"] is None


@pytest.mark.parametrize("fault", ["fingerprint", "duplicate", "window", "timezone", "cost", "unfilled_win", "nonfinite"])
def test_invalid_or_noncomparable_exports_fail_closed(fault):
    export = _export()
    first = export["opportunities"][0]
    if fault == "fingerprint":
        first["new"]["input_sha256"] = "different"
    elif fault == "duplicate":
        export["opportunities"].append(deepcopy(first))
    elif fault == "window":
        first["observed_at"] = "2026-08-01T00:00:00Z"
    elif fault == "timezone":
        first["observed_at"] = "2026-09-01T10:00:00"
    elif fault == "cost":
        first["new"]["roundtrip_cost_r"] = -0.1
    elif fault == "unfilled_win":
        first["new"]["entry_filled"] = False
    else:
        first["new"]["gross_r"] = float("nan")
    with pytest.raises(ValueError):
        compare_exported_cohorts(export)


def test_order_does_not_change_report_and_empty_export_is_unavailable():
    export = _export()
    expected = compare_exported_cohorts(export)
    export["opportunities"].reverse()
    assert compare_exported_cohorts(export) == expected
    export["opportunities"] = []
    empty = compare_exported_cohorts(export)
    assert empty["old"]["total_net_r"] is None
    assert empty["empirical_performance_available"] is False


@pytest.mark.parametrize("fault", ["root", "versions", "window", "opportunity", "boolean_schema"])
def test_malformed_json_shapes_have_explicit_validation_errors(fault):
    export = _export()
    if fault == "root":
        export = []
    elif fault == "versions":
        export["versions"] = ["old", "new"]
    elif fault == "window":
        export["window"] = ["start", "end"]
    elif fault == "opportunity":
        export["opportunities"] = [None]
    else:
        export["schema_version"] = True
    with pytest.raises(ValueError):
        compare_exported_cohorts(export)


def test_simultaneous_exits_are_batched_not_ordered_by_opportunity_id():
    export = _export()
    for row in export["opportunities"]:
        for side in ("old", "new"):
            row[side]["exit_at"] = "2026-09-05T12:00:00Z"
    result = compare_exported_cohorts(export)
    assert result["old"]["max_drawdown_r"] == 0
    assert result["old"]["simultaneous_exit_order_unknown"] is True
    assert result["old"]["max_consecutive_losses"] is None
    export["opportunities"][0]["opportunity_id"] = "z"
    renamed = compare_exported_cohorts(export)
    assert renamed["old"]["max_drawdown_r"] == result["old"]["max_drawdown_r"]


@pytest.mark.parametrize("key", ["market", "direction", "horizon", "timeframe", "regime"])
def test_required_segmentation_cannot_be_omitted(key):
    export = _export()
    del export[key]
    with pytest.raises(ValueError, match=key):
        compare_exported_cohorts(export)
