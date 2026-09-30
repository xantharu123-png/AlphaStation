"""New causal history regressions; run only through the isolated QA harness.

No provider or SMTP is called. The histories are synthetic OHLCV, not copied
scanner payloads, and no eligibility or score threshold is relaxed.
"""
from datetime import datetime, timedelta, timezone
import json
import os

import pytest

import api
from modules.level_zones import classify_for_trade
from modules.vrvp_levels import apply_vrvp_to_trade_setup, build_vrvp_structure


UTC = timezone.utc
CUTOFF = datetime(2026, 9, 29, 20, tzinfo=UTC)


@pytest.mark.parametrize("legacy", [False, True])
def test_pre_repair_strategy_caches_cannot_defer_new_native_plan_scan(monkeypatch, tmp_path, legacy):
    assert api.STOCK_STRATEGY_CACHE_VERSION > 16
    version = 16 if legacy else api.STOCK_STRATEGY_CACHE_VERSION
    path = tmp_path / "shared.json"
    payload = {"results": [], "cache_version": version}
    path.write_text(json.dumps(payload), encoding="utf-8")
    stamp = CUTOFF.timestamp()-30
    os.utime(path, (stamp, stamp))
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", {"strategy_scan": str(path)})
    monkeypatch.setattr(api, "_strategy_cache_path", lambda strategy: str(path))
    assert api._startup_scan_cache_time("strategy_scan", CUTOFF.timestamp()) == (None if legacy else stamp)
    assert json.loads(path.read_text(encoding="utf-8")) == payload


def _history(direction):
    sessions = []
    day = CUTOFF.date()
    while len(sessions) < 80:
        if api.stock_swing.session_close(day.isoformat()) is not None:
            sessions.append(day)
        day -= timedelta(days=1)
    sessions.reverse()
    history = []
    for index, session in enumerate(sessions):
        row = dict(date=session.isoformat(), open=100., high=100.2, low=95.,
                   close=100., volume=1_000_000.)
        if index == 18:
            row["high"] = 114.
        if index == 39:
            row["high"] = 126.
        if index == 79:
            row.update(open=104., high=105.2, low=103., close=105., volume=2_000_000.)
        if direction == "SHORT":
            row.update(open=200-row["open"], high=200-row["low"],
                       low=200-row["high"], close=200-row["close"])
        history.append(row)
    return history


def _native(direction, history=None):
    history = _history(direction) if history is None else history
    entry = next(bar["close"] for bar in history if bar["date"] == "2026-09-29")
    snapshot = api._build_stock_level_snapshot(
        history, symbol="NATV", current_price=entry, direction=direction,
        atr14=2., as_of=CUTOFF, signal_session="2026-09-29", four_hour_bars=[],
    )
    assert snapshot is not None
    diagnostics = {}
    plan = api._build_structured_trade_setup(
        direction, entry, 2., 0., 0., 0., 0., structure_snapshot=snapshot,
        require_causal_structure=True, diagnostics=diagnostics,
    )
    assert plan is not None, diagnostics
    assert plan["tp1_is_projection"] is False
    assert plan["tp2_is_projection"] is False
    assert plan["tp1_zone_id"] != plan["tp2_zone_id"]
    return history, snapshot, plan


def _mail_row(plan, history, direction):
    latest = history[-1]
    return dict(ticker="NATV", Strategy="Structure Reaudit", grade="S", score=90,
        Preis=plan["entry"], direction=direction, Signal_Direction=direction,
        Entry=plan["entry"], StopLoss=plan["stop"], TP1=plan["tp1"], TP2=plan["tp2"],
        trade_setup=plan, rvol=2., Dollar_Volume=200_000_000.,
        Change_Pct=5. if direction == "LONG" else -5., ATR_Pct=5.,
        DayOpen=latest["open"], DayHigh=latest["high"], DayLow=latest["low"],
        Swing_4H_Execution_Status="CLEAR", Swing_Short_4H_Execution_Checked=True,
        Swing_Short_4H_Execution_Status="CLEAR", spread_pct=.01,
        **api.stock_swing.metadata("2026-09-29", plan["entry"]))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_real_vrvp_retains_independent_native_second_target(monkeypatch, direction):
    history, snapshot, plan = _native(direction)
    profile = build_vrvp_structure(
        history, plan["entry"], direction, timeframe="1D", min_bars=30,
        lookback=90, as_of=CUTOFF, date_session_context="us_equity_regular",
    )
    assert profile is not None
    assert snapshot.as_of == CUTOFF
    result = apply_vrvp_to_trade_setup(plan, profile, direction=direction,
                                      asset_type="stock_swing", atr=2.)
    monkeypatch.setattr(api, "_load_common_stock_universe",
                        lambda *a, **kw: ({"NATV"}, "causal_fixture"))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})
    before_row, after_row = (_mail_row(candidate, history, direction)
                             for candidate in (plan, result))
    before_gate = api._alert_trade_plan_rejection_reason(before_row)
    after_gate = api._alert_trade_plan_rejection_reason(after_row)
    assert api._alert_trade_plan_ok(before_row), before_gate
    # Numeric geometry/plan eligibility must not be silently altered by a
    # profile that does not replace the already confirmed opposing zones.
    assert api._alert_trade_levels(after_row)["valid"] is True
    before_state = api._classify_alert_candidate("stock_strategy", before_row, CUTOFF.timestamp())
    after_state = api._classify_alert_candidate("stock_strategy", after_row, CUTOFF.timestamp())
    print({"direction": direction, "native_levels": [plan[k] for k in ("entry", "stop", "tp1", "tp2")],
           "vrvp_levels": [result[k] for k in ("entry", "stop", "tp1", "tp2")],
           "native_mail_gate": before_gate, "vrvp_mail_gate": after_gate,
           "native_classification": before_state["suppression_reasons"],
           "vrvp_classification": after_state["suppression_reasons"]})
    assert api._alert_trade_plan_ok(after_row), after_gate
    assert before_state["suppression_reasons"] == after_state["suppression_reasons"]
    assert result["tp2_is_projection"] is False, {
        "native": {key: value for key, value in plan.items() if key.startswith("tp")},
        "after_vrvp": {key: value for key, value in result.items() if key.startswith("tp")},
    }
    assert result["tp2_zone_id"] == plan["tp2_zone_id"]
    assert result["tp2"] == plan["tp2"]
    assert result["tp1_zone_id"] == plan["tp1_zone_id"]
    assert result["tp1_causal_structure_validated"] is True
    assert result["tp2_causal_structure_validated"] is True
    assert result["level_quality"]["tp1"]["quality"] == "confirmed_zone"
    assert result["level_quality"]["tp2"]["quality"] == "confirmed_zone"
    assert result["tp1_independence_key"] != result["tp2_independence_key"]
    risk = plan["entry"]-result["stop"] if direction == "LONG" else result["stop"]-plan["entry"]
    reward1 = result["tp1"]-plan["entry"] if direction == "LONG" else plan["entry"]-result["tp1"]
    reward2 = result["tp2"]-plan["entry"] if direction == "LONG" else plan["entry"]-result["tp2"]
    assert result["risk"] == pytest.approx(risk)
    assert result["rr_tp1"] == pytest.approx(round(reward1/risk, 2))
    assert result["rr_tp2"] == pytest.approx(round(reward2/risk, 2))
    assert result["rr"] == pytest.approx(round((reward1+reward2)/(2*risk), 2))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_real_vrvp_does_not_downgrade_verified_native_first_target(direction):
    history, _, plan = _native(direction)
    profile = build_vrvp_structure(history, plan["entry"], direction, timeframe="1D", min_bars=30,
        lookback=90, as_of=CUTOFF, date_session_context="us_equity_regular")
    assert profile is not None
    result = apply_vrvp_to_trade_setup(plan, profile, direction=direction,
                                      asset_type="stock_swing", atr=2.)
    assert plan["level_quality"]["tp1"]["quality"] == "confirmed_zone"
    assert result["tp1"] == plan["tp1"]
    assert result["tp1_zone_id"] == plan["tp1_zone_id"]
    assert result["tp1_causal_structure_validated"] is True
    assert result["level_quality"]["tp1"]["quality"] == "confirmed_zone"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_vrvp_does_not_tighten_inside_native_invalidation(direction):
    _, snapshot, plan = _native(direction)
    side = classify_for_trade(snapshot, entry=plan["entry"], direction=direction)
    original = next(zone for zone in side.invalidation_candidates
                    if zone.zone_id == plan["stop_zone_id"])
    # Independent profile acceptance lies closer than the existing invalidation,
    # but is not permission to move a structure-first stop inside that zone.
    boundary = original.lower + .35 if direction == "LONG" else original.upper - .35
    lower, upper = (boundary, boundary+.2) if direction == "LONG" else (boundary-.2, boundary)
    level = dict(price=(lower+upper)/2, source="VRVP POC", kind="POC", weight=2.2,
        source_family="vrvp", timeframe="1D", profile_id="independent-causal-profile",
        independence_key="independent-causal-profile", zone_id="independent-profile-poc",
        zone_low=lower, zone_high=upper, confirmed_at=CUTOFF.isoformat(),
        data_cutoff_at=CUTOFF.isoformat(), causal_structure_validated=True)
    profile = dict(timeframe="1D", supports=[level] if direction == "LONG" else [],
                   resistances=[level] if direction == "SHORT" else [])
    result = apply_vrvp_to_trade_setup(plan, profile, direction=direction,
                                      asset_type="stock_swing", atr=.2)
    assert api._alert_trade_levels(_mail_row(result, _history(direction), direction))["valid"] is True
    print({"direction": direction, "native_stop": plan["stop"], "vrvp_stop": result["stop"],
           "original_zone": [original.lower, original.upper]})
    assert result["stop"] < original.lower if direction == "LONG" else result["stop"] > original.upper
    assert result["stop"] <= plan["stop"] if direction == "LONG" else result["stop"] >= plan["stop"]
    assert result["stop_zone_id"] == plan["stop_zone_id"]
    assert result["stop_source"] == plan["stop_source"]


def _causal_profile_level(lower, upper, name):
    return dict(price=(lower+upper)/2, source="VRVP POC", kind="POC", weight=2.2,
        source_family="vrvp", timeframe="1D", profile_id=name,
        independence_key=name, zone_id=name+"-poc", zone_low=lower, zone_high=upper,
        confirmed_at=CUTOFF.isoformat(), data_cutoff_at=CUTOFF.isoformat(),
        causal_structure_validated=True)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_widened_vrvp_stop_recalculates_rr_and_first_barrier_gate(direction):
    history, _, plan = _native(direction)
    original_risk = abs(plan["entry"]-plan["stop"])
    buffer = plan["entry"]*.0025
    boundary = plan["entry"]-original_risk*1.2+buffer if direction == "LONG" else plan["entry"]+original_risk*1.2-buffer
    lower, upper = (boundary, boundary+.2) if direction == "LONG" else (boundary-.2, boundary)
    level = _causal_profile_level(lower, upper, "causal-wider-stop")
    profile = dict(timeframe="1D", supports=[level] if direction == "LONG" else [],
                   resistances=[level] if direction == "SHORT" else [])
    result = apply_vrvp_to_trade_setup(plan, profile, direction=direction,
                                      asset_type="stock_swing", atr=.2)
    risk = abs(plan["entry"]-result["stop"])
    assert risk > original_risk
    assert result["risk"] == pytest.approx(risk)
    assert result["stop_source"] == "VRVP POC invalidation"
    assert result["stop_zone_id"] == level["zone_id"]
    reward1, reward2 = (abs(result[key]-plan["entry"]) for key in ("tp1", "tp2"))
    assert result["rr_tp1"] == round(reward1/risk, 2)
    assert result["rr_tp2"] == round(reward2/risk, 2)
    assert result["rr"] == round((reward1+reward2)/(2*risk), 2)
    assert result["tp1_zone_id"] == plan["tp1_zone_id"]
    assert result["tp2_zone_id"] == plan["tp2_zone_id"]
    assert result["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert api._alert_trade_plan_ok(_mail_row(result, history, direction)) is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_closer_real_vrvp_barrier_still_is_tp1_and_cannot_be_skipped(direction):
    history, _, plan = _native(direction)
    price = plan["entry"]+1. if direction == "LONG" else plan["entry"]-1.
    level = _causal_profile_level(price-.1, price+.1, "causal-closer-barrier")
    profile = dict(timeframe="1D", supports=[level] if direction == "SHORT" else [],
                   resistances=[level] if direction == "LONG" else [])
    result = apply_vrvp_to_trade_setup(plan, profile, direction=direction,
                                      asset_type="stock_swing", atr=2.)
    assert result["tp1"] == price
    assert result["tp1_zone_id"] == level["zone_id"]
    assert result["tp1_source"] == "VRVP POC"
    assert result["tp2"] == plan["tp1"]
    assert result["tp2_zone_id"] == plan["tp1_zone_id"]
    assert result["stop"] == plan["stop"]
    assert result["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert result["barrier_gate"] == ("BREAK_RECLAIM_REQUIRED" if direction == "LONG" else "BREAK_SUPPORT_REQUIRED")
    assert api._alert_trade_plan_ok(_mail_row(result, history, direction)) is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_projection_flag_cannot_be_upgraded_by_leftover_native_zone_identity(direction):
    history, _, plan = _native(direction)
    setup = dict(plan, tp2_is_projection=True, tp2_source="measured move projection fallback")
    profile = build_vrvp_structure(history, plan["entry"], direction, timeframe="1D", min_bars=30,
        lookback=90, as_of=CUTOFF, date_session_context="us_equity_regular")
    result = apply_vrvp_to_trade_setup(setup, profile, direction=direction,
                                      asset_type="stock_swing", atr=2.)
    assert result["tp2_is_projection"] is True
    assert result["level_quality"]["tp2"]["quality"] == "projection"
    assert result["target_quality"] == "STRUCTURAL_TP1_PROJECTION_TP2"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_future_daily_extremes_cannot_change_native_or_profile_levels(direction):
    history, baseline_snapshot, baseline_plan = _native(direction)
    future = dict(date="2026-09-30", open=200., high=500., low=1., close=300., volume=9_000_000.)
    augmented = [*history, future]
    _, snapshot, plan = _native(direction, augmented)
    assert snapshot.to_dict() == baseline_snapshot.to_dict()
    assert plan == baseline_plan
    profiles = [build_vrvp_structure(bars, plan["entry"], direction, timeframe="1D", min_bars=30,
        lookback=90, as_of=CUTOFF, date_session_context="us_equity_regular") for bars in (history, augmented)]
    assert {key: value for key, value in profiles[0].items() if key != "provenance"} == {
        key: value for key, value in profiles[1].items() if key != "provenance"}
    # Input/exclusion diagnostics correctly count the appended rejected bar;
    # computational provenance and the causally completed prefix stay fixed.
    input_diagnostics = {"input_bar_count", "timestamped_input_count", "date_temporal_input_count",
                         "date_temporal_adapted_count", "excluded_not_causally_completed_count"}
    assert profiles[1]["provenance"]["excluded_not_causally_completed_count"] == 1
    assert {key: value for key, value in profiles[0]["provenance"].items() if key not in input_diagnostics} == {
        key: value for key, value in profiles[1]["provenance"].items() if key not in input_diagnostics}
    finalized = [apply_vrvp_to_trade_setup(plan, profile, direction=direction,
                 asset_type="stock_swing", atr=2.) for profile in profiles]
    assert finalized[0] == finalized[1]
    for prefix in ("stop", "tp1", "tp2"):
        assert datetime.fromisoformat(plan[prefix+"_confirmed_at"].replace("Z", "+00:00")) <= CUTOFF
        assert datetime.fromisoformat(plan[prefix+"_data_cutoff_at"].replace("Z", "+00:00")) == CUTOFF
