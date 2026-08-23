"""Regression contract for target-distance telemetry.

The payload is intentionally descriptive only: callers may display it but
must not turn a configured reachability budget into a trade gate.
"""

import math

import pytest

import modules.trade_levels as trade_levels


def _levels(direction="LONG", **overrides):
    if direction == "SHORT":
        result = {
            "entry": 100.0,
            "stop": 104.0,
            "tp1": 90.0,
            "tp2": 86.0,
            "direction": "SHORT",
            "native": True,
        }
    else:
        result = {
            "entry": 100.0,
            "stop": 96.0,
            "tp1": 110.0,
            "tp2": 114.0,
            "direction": "LONG",
            "native": True,
        }
    result.update(overrides)
    return result


def test_target_reachability_is_symmetric_for_valid_long_and_short_geometry():
    long_payload = trade_levels.target_reachability(
        _levels(), 2.0, horizon="swing"
    )
    short_payload = trade_levels.target_reachability(
        _levels("SHORT"), 2.0, horizon="swing"
    )

    assert long_payload["data_available"] is True
    assert short_payload["data_available"] is True
    assert long_payload["stop_distance_atr"] == short_payload["stop_distance_atr"] == 2.0
    assert long_payload["tp1_distance_atr"] == short_payload["tp1_distance_atr"] == 5.0
    assert long_payload["tp2_distance_atr"] == short_payload["tp2_distance_atr"] == 7.0
    assert long_payload["provenance"] == short_payload["provenance"] == "native"
    assert long_payload["horizon"] == "swing"
    assert long_payload["issues"] == []


@pytest.mark.parametrize("atr", [None, 0, -0.5, math.nan, math.inf, 5e-324])
def test_target_reachability_marks_invalid_atr_unavailable_without_distances(atr):
    payload = trade_levels.target_reachability(_levels(), atr)

    assert payload["data_available"] is False
    assert payload["stop_distance_atr"] is None
    assert payload["tp1_distance_atr"] is None
    assert payload["tp2_distance_atr"] is None
    assert payload["within_budget"] is None
    assert payload["issues"] == ["target_reachability_unavailable"]
    assert "probability" not in payload
    assert not any(
        isinstance(value, float) and not math.isfinite(value)
        for value in payload.values()
    )


def test_target_reachability_marks_invalid_geometry_unavailable():
    payload = trade_levels.target_reachability(
        _levels(stop=104.0), 2.0
    )

    assert payload["data_available"] is False
    assert payload["issues"] == ["target_reachability_unavailable"]


@pytest.mark.parametrize(
    ("level_flags", "expected"),
    [
        ({"native": True}, "native"),
        ({"native": False, "estimated": True}, "estimated"),
        ({"native": True, "synthetic": True}, "synthetic"),
        ({"native": False}, "unknown"),
    ],
)
def test_target_reachability_reports_visible_level_provenance(level_flags, expected):
    payload = trade_levels.target_reachability(_levels(**level_flags), 2.0)

    assert payload["provenance"] == expected


def test_target_reachability_has_no_implicit_budget_and_only_reports_explicit_excess():
    unbudgeted = trade_levels.target_reachability(_levels(), 2.0, horizon="swing")
    budgeted = trade_levels.target_reachability(
        _levels(),
        2.0,
        horizon="swing",
        atr_budgets={"swing": 3.0},
    )

    assert unbudgeted["budget_configured"] is False
    assert unbudgeted["configured_budget_atr"] is None
    assert unbudgeted["within_budget"] is None
    assert unbudgeted["issues"] == []

    assert budgeted["budget_configured"] is True
    assert budgeted["configured_budget_atr"] == 3.0
    assert budgeted["within_budget"] is False
    assert budgeted["issues"] == ["target_beyond_configured_atr_budget"]


def test_target_reachability_plain_text_reports_tp2_budget_as_descriptive_only():
    payload = trade_levels.target_reachability(
        _levels(),
        2.0,
        horizon="swing",
        atr_budgets={"swing": 3.0},
    )

    text = trade_levels.format_target_reachability_text(payload)

    assert "TP2 ueberschreitet das konfigurierte 3.0×ATR-Budget" in text
    assert "deskriptive Telemetrie; kein Mail-Gate; keine Trefferwahrscheinlichkeit" in text
    assert "<" not in text
    assert ">" not in text


def test_target_reachability_plain_text_marks_unavailable_without_gate_claim():
    payload = trade_levels.target_reachability(_levels(), 0)

    text = trade_levels.format_target_reachability_text(payload)

    assert "Reichweiten-Telemetrie: nicht verfuegbar" in text
    assert "deskriptive Telemetrie; kein Mail-Gate; keine Trefferwahrscheinlichkeit" in text
