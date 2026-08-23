import api


def _orb_row(
    *,
    direction="LONG",
    entry=9.42,
    stop=9.24,
    target1=9.72,
    target2=9.88,
):
    return {
        "ticker": "AMPL",
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "vol_confirmed": True,
        "breakout_state": "active_breakout",
        "breakout_age_bars": 2,
        "recent_hold_pct": 0.67,
        "late_to_tp1": False,
    }


def test_ampl_orb_targets_are_valid_percentage_and_r_moves():
    metrics = api._orb_target_plan_metrics(_orb_row())

    assert metrics["valid"] is True
    assert metrics["tp1_rr"] == 1.67
    assert metrics["tp2_rr"] == 2.56
    assert metrics["tp1_move_pct"] == 3.18
    assert metrics["tp2_move_pct"] == 4.88
    assert metrics["target_gap_r"] == 0.89
    assert api._orb_signal_gate_reasons(_orb_row()) == []


def test_orb_reachability_is_telemetry_only_when_an_injected_budget_is_exceeded():
    row = _orb_row()
    row.update(
        {
            "atr": 0.1,
            "trade_horizon": "orb",
            "target_reachability_atr_budgets": {"orb": 1.0},
        }
    )
    without_budget = _orb_row()
    without_budget.update({"atr": 0.1, "trade_horizon": "orb"})

    telemetry_metrics = api._orb_target_plan_metrics(row)
    baseline_metrics = api._orb_target_plan_metrics(without_budget)

    assert telemetry_metrics["valid"] is baseline_metrics["valid"] is True
    assert telemetry_metrics["issues"] == baseline_metrics["issues"] == []
    assert api._orb_signal_gate_reasons(row) == api._orb_signal_gate_reasons(without_budget) == []
    telemetry = telemetry_metrics["target_reachability"]
    assert telemetry["data_available"] is True
    assert telemetry["within_budget"] is False
    assert telemetry["issues"] == ["target_beyond_configured_atr_budget"]


def test_orb_target_validation_is_symmetric_for_shorts():
    row = _orb_row(
        direction="SHORT",
        entry=20.0,
        stop=20.4,
        target1=19.3,
        target2=18.9,
    )
    metrics = api._orb_target_plan_metrics(row)

    assert metrics["valid"] is True
    assert metrics["tp1_rr"] == 1.75
    assert metrics["tp2_rr"] == 2.75
    assert metrics["tp1_move_pct"] == 3.5
    assert metrics["tp2_move_pct"] == 5.5
    assert api._orb_signal_gate_reasons(row) == []


def test_orb_alert_gate_blocks_targets_that_are_too_close():
    row = _orb_row(
        entry=10.0,
        stop=9.8,
        target1=10.31,
        target2=10.35,
    )

    reasons = api._orb_signal_gate_reasons(row)

    assert "orb_target_plan_targets_too_close" in reasons
    assert api._orb_target_plan_metrics(row)["valid"] is False
    assert api._alert_decision_from_reasons("orb", reasons)["decision"] == "NO_TRADE"


def test_orb_alert_gate_blocks_invalid_directional_geometry():
    row = _orb_row(
        entry=10.0,
        stop=9.8,
        target1=10.4,
        target2=10.35,
    )

    reasons = api._orb_signal_gate_reasons(row)

    assert "orb_invalid_target_geometry" in reasons
    assert api._alert_decision_from_reasons("orb", reasons)["decision"] == "NO_TRADE"


def test_orb_target_metrics_accept_legacy_cache_level_names_without_estimating():
    row = _orb_row()
    row.pop("entry")
    row.pop("stop")
    row.pop("target1")
    row.pop("target2")
    row.update(
        {
            "Entry": 9.42,
            "StopLoss": 9.24,
            "TP1": 9.72,
            "TP2": 9.88,
        }
    )

    metrics = api._orb_target_plan_metrics(row)

    assert metrics["valid"] is True
    assert metrics["tp1_rr"] == 1.67
    assert metrics["tp2_rr"] == 2.56


def test_orb_level_sources_name_the_actual_range_projection():
    assert (
        api._humanize_alert_level_source("OR measured move 1.0x range")
        == "Opening Range +1,0x"
    )
    assert (
        api._humanize_alert_level_source("OR measured move 1.5x range")
        == "Opening Range +1,5x"
    )
    assert (
        api._humanize_alert_level_source("OR midpoint tactical stop")
        == "OR-Mitte (taktischer Stop)"
    )
