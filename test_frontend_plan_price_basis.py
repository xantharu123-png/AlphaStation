"""Price/plan checks must not reuse a different observation's execution verdict."""
import json

import pytest

from test_frontend_scanner_lifecycle import SOURCE, node_run


def evaluate(row=None, chart=None, timeframe="4H", detail=None):
    # Run the real browser helper; no provider, SMTP or copied pricing logic.
    start = SOURCE.find("function sidebarPlanAssessment(")
    assert start >= 0, "Sidebar has no common price/plan assessment"
    end = SOURCE.index("// DetailSidebar Component", start)
    program = SOURCE[start:end]
    args = {"row": row or {}, "detail": detail or {}, "chartData": chart,
            "timeframe": timeframe, "ticker": "VIAV"}
    return json.loads(node_run(program + "\nconsole.log(JSON.stringify(sidebarPlanAssessment(" + json.dumps(args) + ")));"))


def row(direction="LONG"):
    return {"ticker": "VIAV", "selected_snapshot": True, "direction": direction,
            "price": 40.68, "snapshot_at": "2026-09-25T21:00:00Z",
            "trade_setup": {"direction": direction, "entry": 40.68,
                            "stop_loss": 39.29, "tp1": 42.86, "tp2": 46.63},
            "trade_health": {"decision": "TRADEABLE", "health_score": 98,
                             "metrics": {"current_price": 40.68, "distance_to_entry_r": 0, "live_rr": 2.92}}}


def chart(price=39.2, **extra):
    return {"ticker": "VIAV", "timeframe": "4H", "candles": [
        {"time": 1790602200, "close": 40.89}, {"time": 1790616600, "close": price}], **extra}


def test_viav_stop_breach_replaces_stored_tradeable_verdict_without_mutating_plan():
    value = evaluate(row(), chart())
    assert value["price"] == 39.2
    assert value["decision"] == "NO_TRADE"
    assert value["state"] == "stop_breached"
    assert value["distanceR"] == -1.06
    assert value["rr"] is None
    assert value["plan"]["entry"] == 40.68
    assert value["historicalHealth"]["health_score"] == 98
    assert value["healthScore"] is None


def test_short_price_above_stop_is_also_invalid():
    short = row("SHORT")
    short["trade_setup"].update(entry=40, stop_loss=42, tp1=36, tp2=34)
    value = evaluate(short, chart(43))
    assert value["state"] == "stop_breached"
    assert value["distanceR"] == -1.5
    assert value["decision"] == "NO_TRADE"


@pytest.mark.parametrize("price,state", [(39.29, "stop_breached"), (40, "price_before_entry"),
                                          (41, "levels_intact"), (42.86, "price_at_or_beyond_target")])
def test_same_price_controls_distance_rr_and_boundary_status(price, state):
    value = evaluate(row(), chart(price))
    assert value["state"] == state
    assert value["healthScore"] is None
    # Geometric checks must never invent a newly confirmed execution signal.
    assert value["decision"] != "TRADEABLE"
    if price == 41:
        assert value["distanceR"] == .23
        assert value["rr"] == 2.19  # ((42.86-41)+(46.63-41))/2/(41-39.29)


@pytest.mark.parametrize("direction,price,previous,label", [
    ("LONG", 40, 41, "Chartkurs unter Entry"),
    ("SHORT", 41, 39, "Chartkurs über Entry"),
])
@pytest.mark.parametrize("include_previous", [True, False])
def test_price_before_entry_does_not_claim_the_entry_was_never_reached(direction, price, previous, label, include_previous):
    selected = row(direction)
    if direction == "SHORT":
        selected["trade_setup"].update(entry=40, stop_loss=42, tp1=36, tp2=34)
    history = chart(price)
    history["candles"][0]["close"] = previous  # Already beyond entry on the previous bar.
    if not include_previous:
        history["candles"] = history["candles"][-1:]
    value = evaluate(selected, history)
    assert value["label"] == label
    assert value["state"] == "price_before_entry"
    assert value["decision"] == "WATCH_ONLY"  # A quote cannot establish trigger history.
    assert value["rr"] is None and value["healthScore"] is None
    assert value["plan"] == selected["trade_setup"]


@pytest.mark.parametrize("direction,price,label", [
    ("LONG", 39.29, "Chartkurs am/unter Stop"),
    ("LONG", 39.2, "Chartkurs am/unter Stop"),
    ("SHORT", 42, "Chartkurs am/über Stop"),
    ("SHORT", 43, "Chartkurs am/über Stop"),
    ("LONG", 42.86, "Chartkurs am/über TP1"),
    ("LONG", 44, "Chartkurs am/über TP1"),
    ("SHORT", 36, "Chartkurs am/unter TP1"),
    ("SHORT", 35, "Chartkurs am/unter TP1"),
])
def test_stop_and_target_labels_describe_price_not_a_recorded_trade_execution(direction, price, label):
    selected = row(direction)
    if direction == "SHORT":
        selected["trade_setup"].update(entry=40, stop_loss=42, tp1=36, tp2=34)
    value = evaluate(selected, chart(price))
    assert value["label"] == label
    assert value["decision"] == "NO_TRADE"
    assert value["rr"] is None and value["healthScore"] is None


@pytest.mark.parametrize("bad", [None, {}, chart(None), chart(True), chart(float("nan")),
    chart(ticker="OTHER"), chart(timeframe="1D"), chart(chart_freshness={"stale": True})])
def test_unavailable_mismatched_or_stale_chart_never_reuses_scanner_execution_verdict(bad):
    value = evaluate(row(), bad)
    assert value["decision"] != "TRADEABLE"
    assert value["state"] == "price_unavailable"
    assert value["distanceR"] is None and value["rr"] is None
    assert value["changePct"] is None


def test_header_change_uses_the_same_valid_chart_observation():
    value = evaluate(row(), chart(39.2))
    assert value["changePct"] == pytest.approx((39.2 / 40.89 - 1) * 100)
    single = chart(39.2)
    single["candles"] = single["candles"][-1:]
    assert evaluate(row(), single)["changePct"] is None


def test_historical_exclusions_survive_without_becoming_a_new_current_verdict():
    selected = row()
    selected["trade_health"] = {"decision": "NO_TRADE", "health_score": 14,
        "exclusion_reasons": ["Negative News-/Dilution-Flags erkannt", ""],
        "warnings": ["Spread am Planstand hoch", "Spread am Planstand hoch", None]}
    value = evaluate(selected, chart(41))
    assert value["state"] == "levels_intact"
    assert value["decision"] != "TRADEABLE"
    assert value["historicalReasons"] == ["Negative News-/Dilution-Flags erkannt", "Spread am Planstand hoch"]


def test_another_detail_plan_cannot_replace_selected_signal_levels():
    detail = {"price": 39.2, "trade_setup": {"entry": 39.2, "stop_loss": 37, "tp1": 43, "tp2": 46},
              "trade_health": {"decision": "TRADEABLE", "health_score": 99}}
    value = evaluate(row(), chart(), detail=detail)
    assert value["plan"]["stop_loss"] == 39.29
    assert value["decision"] == "NO_TRADE"


@pytest.mark.parametrize("update", [{"stop_loss": None}, {"entry": True}, {"tp2": 42.86}, {"direction": "NEUTRAL"}])
def test_invalid_plan_does_not_gain_positive_rr(update):
    selected = row()
    selected["trade_setup"].update(update)
    value = evaluate(selected, chart(41))
    assert value["state"] == "invalid_plan"
    assert value["rr"] is None and value["decision"] == "NO_TRADE"
