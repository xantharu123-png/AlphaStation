"""Cup overrides must finalize the plan, not borrow native-plan decisions.

The fixture runs the real closed-session metric, D/W zone, native-plan,
VRVP, Cup-detector and mail-plan guard code. Only the wall clock and the
external 4H provider are replaced. Run through scripts/run_offline_tests.py.
"""
from datetime import date, datetime, timedelta, timezone
from copy import deepcopy
from dataclasses import replace

import pytest

import api
from test_cup_handle_audit_fixes import _cup_handle_bars, _mk_candidate


SESSION = "2026-08-28"
AS_OF = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 28, 20, 30, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)


def _dated_cup_bars(*, with_overhead=False, far_targets=False):
    # A physically coherent 260-session daily history. The detector reads only
    # its last 180 rows (80 flat rim rows + the complete 100-row Cup). Older
    # observed supply pivots remain available to the native D/W zone builder.
    # The flat 100-close prefix shares high 101.20 with the Cup's first row;
    # that first row is not a newly manufactured local high against a lower
    # artificial padding series.
    prefix = [{"open": 99.7, "high": 101.2, "low": 98.8,
               "close": 100.0, "volume": 1_000_000} for _ in range(160)]
    if with_overhead:
        peaks = ((20, 135.0, 134.0), (60, 118.0, 117.0)) if far_targets else (
            (20, 111.0, 110.0),)
        for index, high, close in peaks:
            # Two closed lower right-hand rows confirm each old daily high.
            ramp = ([102.0, 110.0, 125.0, close, 125.0, 110.0, 102.0]
                    if high == 135.0 else
                    [101.0, 103.0, 110.0, close, 110.0, 103.0, 101.0]
                    if high == 118.0 else
                    [101.0, 103.0, 107.0, close, 107.0, 103.0, 101.0])
            for offset, value in enumerate(ramp, index - 3):
                prefix[offset] = {"open": value * .997, "high": value * 1.012,
                                  "low": value * .988, "close": value,
                                  "volume": 1_000_000}
            prefix[index]["high"] = high
    bars = prefix + _cup_handle_bars(last_bar_date=SESSION)
    sessions = []
    day = date(2026, 8, 28)
    while len(sessions) < len(bars):
        if api.stock_swing.session_close(day.isoformat()) is not None:
            sessions.append(day.isoformat())
        day -= timedelta(days=1)
    for bar, session in zip(bars, reversed(sessions)):
        bar.update(date=session, is_closed=True)
    return bars


def _causal_cup_inputs(monkeypatch, *, with_overhead=True, far_targets=False):
    """Intraday candidate and real, same-history immutable D/W snapshot.

    This helper does not change the clock/session or mock structure/plan/guard
    code. Existing execution tests retain ownership of their current-session
    and completed-5m evidence. Only the external 4H history provider is replaced.
    """
    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", lambda ticker, limit=40: [])
    bars = _dated_cup_bars(with_overhead=with_overhead, far_targets=far_targets)
    candidate = _mk_candidate(bars=bars)
    metrics = api._strategy_daily_history_metrics(
        bars, price=101.7, day_open=101.3949, day_high=102.717,
        day_low=99.8, day_volume=2_400_000, now_utc=AS_OF,
        symbol="CUPX", direction="LONG", include_structure=False,
        signal_session=SESSION)
    snapshot = api._build_stock_level_snapshot(
        bars, symbol="CUPX", current_price=101.7, direction="LONG",
        atr14=metrics["atr14"], as_of=AS_OF,
        four_hour_bars=api._fetch_recent_stock_4h_bars("CUPX", limit=40),
        spread=0.02, signal_session=SESSION)
    assert isinstance(snapshot, api.StructureSnapshot)
    assert snapshot.symbol == "CUPX" and snapshot.as_of == AS_OF
    assert snapshot.completed_bar_counts["1D"] == 260
    assert not api.stock_swing.is_swing(candidate)
    return candidate, snapshot


def _native_then_cup(monkeypatch, *, with_overhead=True, native_direction="LONG"):
    monkeypatch.setattr(api, "datetime", _FixedDatetime)
    monkeypatch.setattr(api.time, "time", lambda: NOW.timestamp())
    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", lambda ticker, limit=40: [])
    bars = _dated_cup_bars(with_overhead=with_overhead)
    candidate = _mk_candidate(bars=bars)
    candidate.update(api.stock_swing.metadata(SESSION, 101.7))
    metrics = api._strategy_daily_history_metrics(
        bars, price=101.7, day_open=101.3949, day_high=102.717,
        day_low=99.8, day_volume=2_400_000, now_utc=AS_OF,
        symbol="CUPX", direction="LONG", include_structure=False,
        signal_session=SESSION)
    context = {"ticker": "CUPX", "daily_bars": bars,
               "history_metrics": metrics, "price": 101.7,
               "prev_atr_pct": 2.5, "day_high": 102.717, "day_low": 99.8,
               "close_pos": 0.82, "bid": 101.69, "ask": 101.71,
               "analysis_as_of": AS_OF, "direction": native_direction}
    snapshot = api._enrich_stock_strategy_native_plan(candidate, context, {})
    if native_direction == "LONG":
        assert candidate.get("native_plan_status") == "built", candidate
        assert candidate.get("trade_setup"), candidate
    else:
        assert candidate["native_plan_status"] == "unavailable"
        assert candidate["native_plan_reason"] == "direction_missing"
    before = deepcopy(candidate)
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000},
        structure_snapshot=snapshot)
    assert final is not None
    # Independent fixture anchors: global left rim 100 * 1.012 = 101.20,
    # trough 75 * .988 = 74.10; measured 0.5/1.0 depth targets 114.75/128.30.
    assert (final["Entry"], final["cup_measured_tp1"], final["cup_measured_tp2"]) == (
        101.2, 114.75, 128.3)
    # Wilder ATR from the 100 OHLC rows is 2.470364563571067; handle low is
    # 94*.988=92.872. 92.872 - .2*ATR = 92.377927 -> delivered stop 92.38.
    assert final["StopLoss"] == 92.38
    assert final["entry_status"] == "SWING_PLAN"
    assert final["trade_action"] == "LONG_TRIGGER"  # not an execution promotion
    return before, final


def test_cup_final_decision_uses_delivered_geometry_not_native_quote(monkeypatch):
    """Copying the native 101.70 entry/stop decision after Cup changes breaks this."""
    native, final = _native_then_cup(monkeypatch)
    assert native["Entry"] == 101.7
    decision = final.get("structure_decision") or {}
    assert decision.get("entry") == 101.2, (decision, final["trade_setup"])
    assert decision.get("stop") == final["StopLoss"]
    assert decision.get("risk") == pytest.approx(101.2 - final["StopLoss"])
    assert decision.get("target1") == final["TP1"]
    assert decision.get("target2") == final["TP2"]
    assert final["TP1"] == 110.75  # confirmed 111 pivot minus .1*ATR90, rounded down


def test_cup_final_setup_and_row_expose_one_structure_decision(monkeypatch):
    """Replacing the nested setup while retaining top-level native metadata breaks this."""
    _, final = _native_then_cup(monkeypatch)
    setup = final["trade_setup"]
    for field in ("structure_decision", "structure_status", "structure_reason",
                  "target_quality", "nearest_barrier", "barrier_gate"):
        assert field in setup, (field, final.get(field), setup)
        assert setup[field] == final[field]


def test_cup_handle_stop_does_not_inherit_native_stop_zone_identity(monkeypatch):
    """A confirmed Cup handle stop cannot be attributed to the old generic zone."""
    native, final = _native_then_cup(monkeypatch)
    original = native["trade_setup"]
    assert original.get("stop_zone_id")
    setup = final["trade_setup"]
    assert setup["stop_source"] != original["stop_source"]
    assert "cup" in setup["stop_source"].lower() or "handle" in setup["stop_source"].lower()
    assert setup.get("stop_zone_id") != original["stop_zone_id"]
    stop_evidence = (setup.get("structure_decision") or {}).get("stop_evidence") or {}
    assert stop_evidence.get("zone_id") != original["stop_zone_id"]
    assert stop_evidence.get("price") == 92.38


def test_cup_final_targets_come_from_real_zones_not_measured_cup_projection(monkeypatch):
    """Measured Cup context must not jump over the confirmed first supply zone."""
    native, final = _native_then_cup(monkeypatch)
    assert native["target_quality"].startswith("STRUCTURAL"), native["trade_setup"]
    assert final["TP1"] == 110.75
    assert final["TP1"] < final["cup_measured_tp1"] == 114.75
    assert final.get("tp1_is_projection") is False
    assert final["trade_setup"].get("tp1_is_projection") is False
    assert final.get("tp2_is_projection") is True  # no second independent overhead pivot
    assert final["target_quality"] == "STRUCTURAL_TP1_PROJECTION_TP2"
    assert api._alert_trade_plan_rejection_reason(final) in {
        "trade_first_barrier_below_minimum_reward", "trade_breakout_not_confirmed"}
    assert final["Trade_Setup_Source"].startswith("cup_handle_1d")


def test_cup_recomputes_real_first_barrier_room_in_cup_risk_units(monkeypatch):
    """A wider Cup stop must not inherit the native stop's more attractive barrier R."""
    native, final = _native_then_cup(monkeypatch)
    barrier = final.get("nearest_barrier")
    assert isinstance(barrier, dict)
    native_barrier = native["nearest_barrier"]
    assert barrier["zone_id"] == native_barrier["zone_id"]
    assert (barrier["zone_low"], barrier["zone_high"]) == (
        native_barrier["zone_low"], native_barrier["zone_high"])
    # The physical zone is immutable; only entry-relative room/R changes.
    # ATR90 from the common final 90 completed daily rows is
    # 2.470084694223651. Native .1*ATR half-width dominates tick/spread noise.
    assert barrier["zone_low"] == pytest.approx(110.75299153057763)
    assert barrier["zone_high"] == pytest.approx(111.24700846942237)
    # Literal, independently hand-checked geometry: conservative room to the
    # unchanged physical lower boundary / Cup risk 8.82 = 1.0831056157117491R.
    assert barrier["distance_r"] == pytest.approx(1.0831056157117491, abs=0.005)
    assert final["barrier_gate"] == "BREAK_RECLAIM_REQUIRED"
    assert final["structure_status"] == "WAIT_BREAK_RECLAIM"


def test_cup_projection_rejection_is_bound_to_cup_not_old_native_risk(monkeypatch):
    """With no overhead evidence, safe rejection is retained with current geometry."""
    native, final = _native_then_cup(monkeypatch, with_overhead=False)
    assert native["target_quality"].startswith("PROJECTION_ONLY")
    assert api._alert_trade_plan_rejection_reason(final) == "trade_target_not_structural"
    decision = final.get("structure_decision") or {}
    assert decision.get("entry") == 101.2
    assert decision.get("stop") == final["StopLoss"]
    assert decision.get("target1") == final["TP1"]
    assert decision.get("target2") == final["TP2"]
    assert final["target_quality"].startswith("PROJECTION_ONLY")


def test_valid_cup_without_native_snapshot_cannot_borrow_trade_authority(monkeypatch):
    """A real unavailable native-plan path leaves measured context blocked."""
    native, final = _native_then_cup(monkeypatch, native_direction="")
    assert native["native_plan_status"] == "unavailable"
    assert final["structure_status"] in {"STRUCTURE_UNAVAILABLE", "REJECT"}
    assert final["target_quality"].startswith("PROJECTION_ONLY")
    assert final.get("tp1_is_projection") is True
    assert final["trade_setup"].get("tp1_is_projection") is True
    assert api._alert_trade_plan_rejection_reason(final) == "trade_target_not_structural"
    assert final.get("barrier_gate") not in {"BREAK_RECLAIM_REQUIRED", "BREAK_SUPPORT_REQUIRED"}
    assert final.get("entry_eligible") is not True


def test_cup_with_two_independent_far_zones_has_honest_admissible_structural_plan(monkeypatch):
    """Real confirmed first/second zones can pass, without authorizing measured targets."""
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    monkeypatch.setattr(api, "datetime", _FixedDatetime)
    monkeypatch.setattr(api.time, "time", lambda: NOW.timestamp())
    candidate.update(api.stock_swing.metadata(SESSION, 101.7))
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=snapshot)
    assert final is not None
    assert (final["Entry"], final["StopLoss"], final["TP1"], final["TP2"]) == (
        101.2, 92.38, 117.75, 134.75)
    assert (final["cup_measured_tp1"], final["cup_measured_tp2"]) == (114.75, 128.3)
    assert final["tp1_is_projection"] is False
    assert final["tp2_is_projection"] is False
    assert final["trade_setup"]["tp1_zone_id"] != final["trade_setup"]["tp2_zone_id"]
    assert final["structure_status"] == "ACCEPT"
    assert final["barrier_gate"] is None
    assert api._alert_trade_plan_rejection_reason(final) is None
    # Plan admissibility must not manufacture realtime execution confirmation.
    assert final["entry_status"] == "SWING_PLAN"
    assert final["trade_action"] == "LONG_TRIGGER"
    assert final.get("fill_evidence_verified") is False


def _pin_cup_clock(monkeypatch, now=NOW):
    class ClockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", ClockDatetime)
    monkeypatch.setattr(api.time, "time", lambda: now.timestamp())


def _assert_cup_without_snapshot_authority(final):
    assert final is not None
    assert final["structure_status"] == "REJECT"
    assert final["structure_reason"] == "causal_structure_missing"
    assert final["target_quality"].startswith("PROJECTION_ONLY")
    assert (final["Entry"], final["StopLoss"], final["TP1"], final["TP2"]) == (
        101.2, 92.38, 114.75, 128.3)
    assert final["tp1_is_projection"] is True
    assert final["tp2_is_projection"] is True
    assert final["nearest_barrier"] is None
    assert final["entry_eligible"] is False
    assert final["structure_decision"] == final["trade_setup"]["structure_decision"]
    assert api._alert_trade_plan_rejection_reason(final) == "trade_target_not_structural"


def test_cup_cannot_borrow_causal_zones_from_another_symbol(monkeypatch):
    """A same-price/same-date foreign snapshot is not the candidate's evidence."""
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    _pin_cup_clock(monkeypatch)
    candidate.update(api.stock_swing.metadata(SESSION, 101.7))
    foreign = replace(snapshot, symbol="OTHER")
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=foreign)
    _assert_cup_without_snapshot_authority(final)


@pytest.mark.parametrize("other_cutoff", [
    datetime(2026, 8, 27, 20, 0, tzinfo=timezone.utc),
    datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
], ids=["previous_completed_swing_session", "next_completed_swing_session"])
def test_cup_snapshot_must_match_its_bound_swing_close(monkeypatch, other_cutoff):
    """Neither neighboring completed session may replace the bound Aug28 evidence."""
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    # Both supplied cutoffs are in the past: this specifically exercises the
    # swing-session binding, not a future-clock rejection of the Aug31 case.
    _pin_cup_clock(monkeypatch, datetime(2026, 9, 1, 20, 30, tzinfo=timezone.utc))
    candidate.update(api.stock_swing.metadata(SESSION, 101.7))
    wrong_session = replace(snapshot, as_of=other_cutoff)
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=wrong_session)
    _assert_cup_without_snapshot_authority(final)


@pytest.mark.parametrize("invalid_cutoff", [
    datetime(2026, 8, 28, 21, 30, tzinfo=timezone.utc),
    datetime(2026, 8, 28, 19, 59, tzinfo=timezone.utc),
], ids=["future_snapshot", "before_daily_confirmation_close"])
def test_intraday_cup_rejects_snapshot_outside_its_causal_time_bounds(
    monkeypatch, invalid_cutoff,
):
    """Intraday rows without a swing binding still need actual causal time bounds."""
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    _pin_cup_clock(monkeypatch)
    # A deterministic unavailable session keeps this structure-boundary test
    # out of the independent next-session queue/5m execution workflow.
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {
        "allowed": False, "session": "UNKNOWN", "market_time": None,
        "reason": "fixed unavailable session for causal-boundary QA"})
    invalid = replace(snapshot, as_of=invalid_cutoff)
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=invalid)
    _assert_cup_without_snapshot_authority(final)


def test_cup_finalization_does_not_mutate_the_source_structure_snapshot(monkeypatch):
    """Cup classification/VRVP cannot change another consumer's source zones."""
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    _pin_cup_clock(monkeypatch)
    candidate.update(api.stock_swing.metadata(SESSION, 101.7))
    before = deepcopy(snapshot.to_dict())
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=snapshot)
    assert final is not None
    assert api._alert_trade_plan_rejection_reason(final) is None
    assert snapshot.to_dict() == before
    assert snapshot.current_price == 101.7  # source quote, not Cup entry 101.20


def test_forming_next_session_daily_bar_cannot_replace_closed_cup_plan(monkeypatch):
    """Today's unfinished high wick must not rewrite Friday's confirmed plan."""
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    monday_now = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)
    _pin_cup_clock(monkeypatch, monday_now)
    assert api._stock_trade_email_status()["allowed"] is True
    candidate.update(api.stock_swing.metadata(SESSION, 101.7))
    forming = {"date": "2026-08-31", "open": 101.7, "high": 150.0,
               "low": 101.0, "close": 101.7, "volume": 100_000_000,
               "is_closed": False}
    candidate["_daily_bars"].append(forming)
    before = deepcopy(snapshot.to_dict())
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=snapshot)
    assert final is not None
    assert final["last_daily_bar_date"] == SESSION
    assert final["daily_close_confirmation_date"] == SESSION
    assert final["cup_confirmation_close"] == 101.7
    assert (final["Entry"], final["StopLoss"], final["TP1"], final["TP2"]) == (
        101.2, 92.38, 117.75, 134.75)
    assert (final["cup_measured_tp1"], final["cup_measured_tp2"]) == (114.75, 128.3)
    assert api._alert_trade_plan_rejection_reason(final) is None
    assert final["entry_status"] == "SWING_PLAN"
    assert final["trade_action"] == "LONG_TRIGGER"
    assert final.get("fill_evidence_verified") is False
    assert snapshot.to_dict() == before
    assert candidate["_daily_bars"][-1] == forming


@pytest.mark.parametrize("corruption", [
    "missing_row_receipt", "missing_setup_receipt", "missing_both_receipts", "old_receipt",
    "decision_mismatch", "top_decision_only", "geometry_alias_mismatch",
    "nested_target_only", "metadata_mismatch",
    "both_decisions_wrong_direction", "nested_stop_alias_mismatch",
])
def test_cup_guard_rejects_legacy_or_incoherent_final_plan_receipts(monkeypatch, corruption):
    """Cached/watched Cup geometry cannot bypass finalization with stale aliases."""
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    _pin_cup_clock(monkeypatch)
    candidate.update(api.stock_swing.metadata(SESSION, 101.7))
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=snapshot)
    assert final is not None
    assert api._cup_signal_contract_reason(final) is None
    assert api._alert_trade_plan_rejection_reason(final) is None
    damaged = deepcopy(final)
    if corruption == "missing_row_receipt":
        damaged.pop("cup_plan_version")
    elif corruption == "missing_setup_receipt":
        damaged["trade_setup"].pop("cup_plan_version")
    elif corruption == "missing_both_receipts":
        damaged.pop("cup_plan_version")
        damaged["trade_setup"].pop("cup_plan_version")
    elif corruption == "old_receipt":
        damaged["cup_plan_version"] = "legacy_measured_only"
        damaged["trade_setup"]["cup_plan_version"] = "legacy_measured_only"
    elif corruption == "decision_mismatch":
        damaged["trade_setup"]["structure_decision"] = {
            **damaged["trade_setup"]["structure_decision"], "entry": 101.7}
    elif corruption == "top_decision_only":
        damaged["structure_decision"] = {**damaged["structure_decision"], "target1": 114.75}
    elif corruption == "geometry_alias_mismatch":
        damaged["TP1"] = 114.75  # measured context is not delivered TP1 117.75
    elif corruption == "nested_target_only":
        damaged["trade_setup"]["tp1"] = 114.75
    elif corruption == "metadata_mismatch":
        damaged["trade_setup"]["target_quality"] = "PROJECTION_ONLY_LEGACY"
    elif corruption == "both_decisions_wrong_direction":
        damaged["structure_decision"]["direction"] = "SHORT"
        damaged["trade_setup"]["structure_decision"]["direction"] = "SHORT"
    elif corruption == "nested_stop_alias_mismatch":
        damaged["trade_setup"]["stop_loss"] = 95.0
    assert api._cup_final_plan_contract_reason(damaged) is not None
    assert api._alert_trade_plan_rejection_reason(damaged) == "trade_cup_final_plan_not_confirmed"
    provider_requests = []

    class UnneededProviderResponse:
        status_code = 503

        def json(self):
            return {"status": "ERROR", "results": []}

    def record_provider_request(url, *args, **kwargs):
        provider_requests.append(str(url))
        return UnneededProviderResponse()

    monkeypatch.setattr(api, "rate_limited_get", record_provider_request)
    assert api._revalidate_stock_strategy_mail_candidate(
        damaged, now_ts=NOW.timestamp(), scanner_name="stock_strategy") == {
            "ok": False, "reason": "trade_cup_final_plan_not_confirmed"}
    assert api._revalidate_stock_swing_plan(
        damaged, now_ts=NOW.timestamp(), scanner_name="stock_strategy") == {
            "ok": False, "reason": "trade_cup_final_plan_not_confirmed"}
    assert provider_requests == []


def test_cup_stop_confirmation_keeps_pattern_close_not_later_analysis_cutoff(monkeypatch):
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    later_cutoff = AS_OF + timedelta(minutes=15)
    _pin_cup_clock(monkeypatch, later_cutoff + timedelta(minutes=1))
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {
        "allowed": False, "session": "UNKNOWN", "reason": "isolated structure test"})
    # Build the same closed signal's genuine causal zones at a later cutoff.
    # Its explicit session reference remains before the signal, as required
    # by the real level builder; no old serialized verdict is substituted.
    daily = api._daily_level_bars(candidate["_daily_bars"])
    later_snapshot = api.build_structure_snapshot(
        {"1D": daily, "1W": api._completed_weekly_level_bars(
            candidate["_daily_bars"], as_of=later_cutoff)},
        symbol="CUPX", asset_class="stock", horizon="swing",
        current_price=101.7, tick_size=0.01, spread=0.02,
        atr_by_timeframe=snapshot.atr_by_timeframe, as_of=later_cutoff,
        pivot_left=2, pivot_right=2, timestamp_mode="open",
        include_session_levels=True, session_reference_before=daily[-1]["open_time"])
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000},
        structure_snapshot=later_snapshot)
    assert final is not None and api._alert_trade_plan_rejection_reason(final) is None, final["trade_setup"]
    setup = final["trade_setup"]
    assert setup["stop_confirmed_at"] == AS_OF.isoformat()
    assert setup["stop_data_cutoff_at"] == later_cutoff.isoformat()
    assert setup["structure_decision"]["stop_evidence"]["confirmed_at"] == AS_OF.isoformat()


def test_undated_cup_cannot_borrow_a_dated_snapshot_for_stop_provenance(monkeypatch):
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    _pin_cup_clock(monkeypatch)
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {
        "allowed": False, "session": "UNKNOWN", "reason": "isolated structure test"})
    for bar in candidate["_daily_bars"]:
        bar.pop("date")
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=snapshot)
    _assert_cup_without_snapshot_authority(final)


def test_projection_fallback_preserves_specific_rejected_barrier_reason(monkeypatch):
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    _pin_cup_clock(monkeypatch)
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {
        "allowed": False, "session": "UNKNOWN", "reason": "isolated structure test"})
    # The actual typed snapshot's unavailable-data flag must yield the real
    # builder's specific negative decision, not new projection authority.
    changed_snapshot = replace(
        snapshot, quality_flags=tuple(snapshot.quality_flags) + ("structure_unavailable",))
    diagnostics = {}
    built = api._build_structured_trade_setup(
        "LONG", 101.2, changed_snapshot.atr_by_timeframe["1D"],
        None, None, None, None, structure_snapshot=changed_snapshot,
        require_causal_structure=True, pattern_invalidation_stop=92.38,
        pattern_invalidation_source="confirmed cup handle low (1D) invalidation",
        diagnostics=diagnostics)
    assert built is None
    assert diagnostics["reason"] == "causal_structure_unavailable"
    final = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=changed_snapshot)
    assert final["structure_status"] == "REJECT"
    assert final["structure_reason"] == diagnostics["reason"]
    assert final["trade_setup"]["structure_reason"] == diagnostics["reason"]
    assert final["structure_decision"]["reason"] == diagnostics["reason"]
    assert final["nearest_barrier"] is None
    assert final["tp1_is_projection"] is True
    assert api._alert_trade_plan_rejection_reason(final) == "trade_target_not_structural"
