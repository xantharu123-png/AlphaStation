"""Cached stock release uses existing mail selection gates, never transport.

These regressions catch a stock-quality rejection omitted from App/Admin,
invalid daily evidence silently falling back to a live row, and derived
display fields feeding back into a second assessment. All gates are real;
only reference storage and the clock are controlled, with external I/O denied
by scripts/run_offline_tests.py.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

import api
from modules import stock_swing_contract as swing
from modules.breakout_warnings import breakout_warning_fields


NOW = datetime(2026, 9, 15, 18, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_cached_evidence(monkeypatch, tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(swing, "datetime", Clock)
    monkeypatch.setattr(api.time, "time", lambda: NOW.timestamp())
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {
        "tickers": ["TEST"], "names": {"TEST": "Test Corporation"},
        "loaded_at": NOW.timestamp(),
    })
    # App's universe API is a cache-only adapter; Admin reads its real memory
    # cache directly. No asset guard, score, plan or quality gate is replaced.
    monkeypatch.setattr(api, "_load_common_stock_universe",
                        lambda *a, **k: ({"TEST"}, "fixture_cached"))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "starter_swing")


def daily_row(**changes):
    row = {
        "ticker": "TEST", "Ticker": "TEST", "Strategy": "Momentum Breakout Long",
        "direction": "LONG", "Signal_Direction": "LONG", "price": 100., "Preis": 100.,
        "score": 95, "grade": "S", "RVOL": 3., "Volume": 3_000_000,
        "ATR14": 3., "Change_Pct": 2., "Close_Position": .95,
        "Upper_Wick_Pct": 5., "Day_High": 101., "Day_Low": 96., "Day_Open": 98.,
        "History_OK": True, "MedianDollarVol20": 100_000_000,
        "Momentum_Contract_Version": api.MOMENTUM_CONTRACT_VERSION,
        "Momentum_Execution_Confirmed": False, "Momentum_Breakout_Type": "20D_HIGH_BREAKOUT",
        "Breakout_Level": 99., "Breakout_Freshness_Checked": True,
        "Breakout_Freshness_Status": "DAILY_CONFIRMED", "Breakout_Confirmation_Timeframe": "1D",
        "Breakout_Confirmation_Close": 100., "Breakout_Continuation_Score": 96,
        "Breakout_Continuation_Status": "CONTINUATION_OK", "Breakout_Fakeout_Risk": "LOW",
        "entry": 100., "stop_loss": 95., "tp1": 110., "tp2": 120.,
        "trade_setup": {"direction": "LONG", "entry": 100., "stop": 95.,
                        "tp1": 110., "tp2": 120.},
        **breakout_warning_fields(confirmed_close=True),
        **swing.metadata("2026-09-14", 100.),
    }
    row["Breakout_Confirmation_Closed_At"] = row["scan_price_observed_at"]
    row.update(changes)
    return row


def read_only_audit(tmp_path, row, scanner="stock_strategy"):
    cache = tmp_path / (scanner + ".json")
    api.save_cache_file(str(cache), [row])
    before = cache.read_bytes()
    audit = api._build_alert_audit_for_cache(scanner, str(cache), read_only=True)
    assert cache.read_bytes() == before
    return audit


def mail_codes(state):
    return [item["code"] for item in state["mail_check"]["reasons"]]


@pytest.mark.parametrize("scanner", ["stock_strategy", "strategy_scan"])
def test_daily_quality_70_cannot_be_released_when_same_cached_mail_check_blocks(scanner, tmp_path):
    row = daily_row(Breakout_Continuation_Score=70,
                    Breakout_Continuation_Status="CONTINUATION_WATCH")
    before = deepcopy(row)
    state = api._scanner_result_trade_state(scanner, row)
    assert state["alertable_now"] is False
    assert state["decision"] == "WATCH"
    assert "momentum_mail_blocked_daily_quality_below_threshold" in state["display_reasons"]
    assert state["mail_check"]["status"] == "blocked"
    assert "momentum_mail_blocked_daily_quality_below_threshold" in mail_codes(state)
    audit = read_only_audit(tmp_path, row, scanner)
    assert audit["precheck_passed_count"] == 0
    assert audit["suppression_counts"]["momentum_mail_blocked_daily_quality_below_threshold"] == 1
    assert row == before


@pytest.mark.parametrize("score", [78, 96])
def test_existing_quality_floor_passes_cached_selection_without_claiming_delivery(score, tmp_path):
    row = daily_row(Breakout_Continuation_Score=score)
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert state["alertable_now"] is True, state
    assert state["decision"] == "TRADE_NOW"
    assert state["mail_check"]["status"] == "checks_passed"
    assert state["mail_check"]["semantics"] == "read_only_precheck_not_delivery_or_send_permission"
    audit = read_only_audit(tmp_path, row)
    assert audit["precheck_passed_count"] == 1
    assert audit["evidence_scope"] == "cache_precheck"
    assert audit["delivery_evaluated"] is False


@pytest.mark.parametrize("changes,reason", [
    ({"Swing_4H_Execution_Status": "RUN_EXTENDED"}, "stock_swing_mail_blocked_4h_extended_run"),
    ({"Swing_4H_Execution_Status": "WAIT_RECLAIM"}, "stock_swing_mail_blocked_4h_rejection"),
    ({"Swing_4H_Execution_Status": "DATA_UNAVAILABLE"}, "stock_swing_mail_blocked_missing_4h_state"),
    ({"ATR14": .5}, "stock_swing_mail_blocked_low_volatility_budget"),
    ({"Business_Severe_Risk": True}, "stock_swing_mail_blocked_severe_business_risk"),
    ({"History_OK": False}, "momentum_mail_blocked_missing_liquidity_history"),
    ({"MedianDollarVol20": 0}, "momentum_mail_blocked_missing_liquidity_history"),
    ({"MedianDollarVol20": 100_000}, "momentum_mail_blocked_thin_baseline_liquidity"),
    ({"Momentum_Breakout_Type": ""}, "momentum_mail_blocked_missing_breakout_type"),
    ({"Momentum_Breakout_Type": "TREND_RECLAIM"}, "momentum_mail_blocked_trend_reclaim_not_breakout"),
    ({"Momentum_Breakout_Type": "OTHER"}, "momentum_mail_blocked_unknown_breakout_type"),
    ({"Breakout_Freshness_Status": "NOT_CONFIRMED"}, "momentum_breakout_freshness_unconfirmed"),
    ({"Breakout_Freshness_Status": "STALE_BREAKOUT"}, "momentum_breakout_stale_wait_trigger"),
    ({"Breakout_Continuation_Score": None}, "momentum_mail_blocked_daily_quality_unavailable"),
    ({"Breakout_Continuation_Status": "WICK_WATCH", "Breakout_Continuation_Score": 80}, "momentum_mail_blocked_daily_quality_unconfirmed"),
    ({"Breakout_Fakeout_Risk": "HIGH"}, "momentum_mail_blocked_fakeout_risk"),
    ({"Upper_Wick_Pct": 38}, "momentum_mail_blocked_upper_wick"),
    ({"Close_Position": .6}, "momentum_mail_blocked_not_holding_upper_range"),
    ({"RVOL": 1.4}, "momentum_mail_blocked_rvol_below_breakout_floor"),
    ({"Day_High": 110.}, "momentum_mail_blocked_daily_target_previously_touched"),
    ({"Day_High": 106., "Close_Position": .8}, "momentum_mail_blocked_spike_rejected_from_high"),
    ({"Change_Pct": 8., "Momentum_Breakout_Type": "10D_HIGH_BREAKOUT"}, "momentum_mail_blocked_daily_move_extended"),
    ({"Momentum_Breakout_Type": "RANGE_BREAKOUT"}, "momentum_mail_blocked_range_not_near_breakout_high"),
])
def test_existing_stock_specific_quality_blocker_reaches_app_and_admin(changes, reason, tmp_path):
    row = daily_row(**changes)
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert reason in state["display_reasons"]
    assert state["alertable_now"] is False
    assert reason in mail_codes(state)
    audit = read_only_audit(tmp_path, row)
    assert audit["precheck_passed_count"] == 0
    assert audit["suppression_counts"][reason] == 1


def test_quality_pass_cannot_override_generic_invalid_trade_plan(tmp_path):
    row = daily_row(trade_setup={"direction": "LONG", "entry": 100., "stop": 105.,
                                "tp1": 110., "tp2": 120.}, stop_loss=105.)
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "invalid_trade_plan" in state["display_reasons"]
    assert state["mail_check"]["status"] == "blocked"
    assert read_only_audit(tmp_path, row)["precheck_passed_count"] == 0


@pytest.mark.parametrize("changes", [
    swing.metadata("2026-09-11", 100.),
    swing.metadata("2026-09-15", 100.),
    {"scan_price_source": "polygon_snapshot"},
    {"scan_price_observed_at": "2026-09-15T20:00:00Z"},
])
def test_invalid_stale_future_or_mismatched_daily_reference_never_falls_back_to_live(changes, tmp_path):
    row = daily_row(**changes)
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "swing_daily_reference_invalid_or_stale" in state["display_reasons"]
    assert state["mail_check"]["status"] == "blocked"
    audit = read_only_audit(tmp_path, row)
    assert audit["suppression_counts"]["swing_daily_reference_invalid_or_stale"] == 1
    assert audit["precheck_passed_count"] == 0


def test_daily_reference_price_mismatch_cannot_pass_cached_selection(tmp_path):
    row = daily_row(swing_reference_close=101.)
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "swing_reference_price_mismatch" in state["display_reasons"]
    assert "swing_reference_price_mismatch" in mail_codes(state)
    audit = read_only_audit(tmp_path, row)
    assert audit["precheck_passed_count"] == 0
    assert audit["suppression_counts"]["swing_reference_price_mismatch"] == 1


@pytest.mark.parametrize("price,allowed", [(100.005, True), (100.0053, False),
                                          (99.995, True), (99.9947, False)])
def test_cached_reference_price_uses_existing_sender_tolerance_inside_and_outside(price, allowed, tmp_path):
    row = daily_row(price=price, Preis=price)
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert state["alertable_now"] is allowed
    assert ("swing_reference_price_mismatch" in state["display_reasons"]) is not allowed
    assert state["mail_check"]["status"] == ("checks_passed" if allowed else "blocked")
    audit = read_only_audit(tmp_path, row)
    assert audit["precheck_passed_count"] == int(allowed)


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -float("inf"), None])
def test_nonfinite_or_missing_cached_price_cannot_match_finite_daily_reference(price, tmp_path):
    # The existing extractor recognizes entry as a legacy price alias. Remove
    # that alias too when testing absence; never change production precedence.
    row = daily_row(price=price, Preis=price, entry=None)
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "swing_reference_price_mismatch" in state["display_reasons"]
    assert state["mail_check"]["status"] == "blocked"
    assert read_only_audit(tmp_path, row)["precheck_passed_count"] == 0


@pytest.mark.parametrize("changes,reason", [
    ({"Swing_Short_4H_Execution_Status": "WAIT_BREAKDOWN"}, "stock_swing_short_mail_blocked_wait_4h_breakdown"),
    ({"Swing_Short_4H_Execution_Status": "WAIT_RETEST"}, "stock_swing_short_mail_blocked_wait_4h_retest"),
    ({"Swing_Short_4H_Execution_Status": "DATA_UNAVAILABLE"}, "stock_swing_short_mail_blocked_missing_4h_state"),
    ({"Swing_Short_4H_Post_Parabolic": True, "Swing_Short_4H_Stop_Geometry_Valid": False}, "stock_swing_short_mail_blocked_invalid_4h_stop_geometry"),
    ({"Swing_Short_4H_Post_Parabolic": True, "Swing_Short_4H_Stop_Geometry_Valid": True, "Swing_Short_4H_Required_Stop": 106.}, "stock_swing_short_mail_blocked_stop_inside_4h_base"),
])
def test_short_specific_quality_blocker_reaches_app_and_admin(changes, reason, tmp_path):
    row = daily_row(Strategy="Gap Momentum Short", direction="SHORT", Signal_Direction="SHORT",
                    Change_Pct=-3., Close_Position=.2, Day_Open=103.,
                    entry=100., stop_loss=105., tp1=90., tp2=80.,
                    trade_setup={"direction": "SHORT", "entry": 100., "stop": 105., "tp1": 90., "tp2": 80.},
                    Swing_Short_4H_Execution_Checked=True, **changes)
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert reason in state["display_reasons"]
    assert state["alertable_now"] is False
    assert reason in mail_codes(state)
    # Dedicated Gap metadata checks have their own existing stale warning;
    # this test still requires the same stock-quality reason in the precheck.
    audit = read_only_audit(tmp_path, row)
    assert audit["precheck_passed_count"] == 0
    assert audit["suppression_counts"][reason] == 1


def test_late_session_legacy_quality_gate_is_not_lost_without_daily_evidence(monkeypatch, tmp_path):
    late = datetime(2026, 9, 15, 19, 50, tzinfo=timezone.utc)
    monkeypatch.setattr(api.time, "time", lambda: late.timestamp())
    row = daily_row(stock_swing_mode=None)
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "momentum_mail_blocked_late_session_without_daily_close" in state["display_reasons"]
    audit = read_only_audit(tmp_path, row)
    assert audit["precheck_passed_count"] == 0
    assert audit["suppression_counts"]["momentum_mail_blocked_late_session_without_daily_close"] == 1


def test_unreadable_stock_quality_is_unavailable_not_green_release(monkeypatch, tmp_path):
    def unreadable(*a, **k):
        raise ValueError("fixture unreadable quality")

    monkeypatch.setattr(api, "_stock_strategy_mail_quality_state", unreadable)
    row = daily_row()
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "display_metadata_invalid" in state["display_reasons"]
    assert state["cached_admission_complete"] is False
    assert state["mail_check"]["status"] == "unavailable"
    audit = read_only_audit(tmp_path, row)
    assert audit["precheck_passed_count"] == 0
    assert audit["suppression_counts"]["display_metadata_invalid"] == 1


@pytest.mark.parametrize("scanner", ["stock_strategy", "strategy_scan"])
@pytest.mark.parametrize("reason,changes,mail_status", [
    ("display_metadata_invalid", {}, "unavailable"),
    ("swing_daily_reference_invalid_or_stale", swing.metadata("2026-09-11", 100.), "blocked"),
    ("swing_reference_price_mismatch", {"swing_reference_close": 101.}, "blocked"),
])
def test_cached_data_errors_are_no_trade_not_observable_watch_candidates(scanner, reason, changes, mail_status, monkeypatch, tmp_path):
    if reason == "display_metadata_invalid":
        def unreadable(*a, **k):
            raise ValueError("fixture unreadable quality")

        monkeypatch.setattr(api, "_stock_strategy_mail_quality_state", unreadable)
    row = daily_row(**changes)
    before = deepcopy(row)
    cached = api._cached_alert_admission_state(scanner, row, NOW.timestamp())
    assert reason in cached["suppression_reasons"]
    assert cached["alertable_now"] is False
    assert cached["decision"] == "NO_TRADE"
    displayed = api._scanner_result_trade_state(scanner, row)
    assert displayed["decision"] == "NO_TRADE"
    assert displayed["alertable_now"] is False
    assert reason in displayed["display_reasons"]
    assert displayed["mail_check"]["status"] == mail_status
    assert row == before
    audit = read_only_audit(tmp_path, row, scanner)
    assert audit["precheck_passed_count"] == 0
    assert audit["decision_counts"] == {"NO_TRADE": 1}
    assert audit["suppression_counts"][reason] == 1
    api._apply_scanner_result_trade_state(row, scanner)
    assert row["scanner_decision"] == "NO_TRADE"
    assert row["trade_signal"] == "NICHT_TRADEN"
    assert row["trade_action"] == "NO_TRADE"


@pytest.mark.parametrize("scanner", ["bi_long", "bi_short"])
def test_bi_selection_does_not_acquire_stock_strategy_quality_gates(scanner, monkeypatch):
    monkeypatch.setattr(api, "_stock_strategy_mail_quality_state",
                        lambda *a, **k: pytest.fail("BI is not a stock strategy mail"))
    row = daily_row(Strategy="BI", grade="B", score=69, BI_Grade="B", BI_Score=69)
    state = api._scanner_result_trade_state(scanner, row)
    assert state["alertable_now"] is False
    assert state["decision"] == "NOT_RELEASED"
    assert "grade_below_alert_threshold" in state["display_reasons"]
    assert state["mail_check"]["status"] == "blocked"


@pytest.mark.parametrize("quality_score,status", [(70, "CONTINUATION_WATCH"), (96, "CONTINUATION_OK")])
def test_redecoration_is_dict_and_byte_idempotent_on_producer_basis(quality_score, status):
    row = daily_row(Breakout_Continuation_Score=quality_score, Breakout_Continuation_Status=status)
    api._apply_scanner_result_trade_state(row, "stock_strategy")
    before = deepcopy(row)
    serialized = json.dumps(row, sort_keys=True).encode()
    api._apply_scanner_result_trade_state(row, "stock_strategy")
    assert row == before
    assert json.dumps(row, sort_keys=True).encode() == serialized
    assert row["mail_check"]["status"] == ("blocked" if quality_score == 70 else "checks_passed")


def test_admin_cache_reassessment_uses_producer_action_not_derived_watch_action(tmp_path):
    row = daily_row(Breakout_Continuation_Score=70, Breakout_Continuation_Status="CONTINUATION_WATCH")
    api._apply_scanner_result_trade_state(row, "stock_strategy")
    assert row["trade_signal"] == "BEOBACHTEN"
    audit = read_only_audit(tmp_path, row)
    assert audit["precheck_passed_count"] == 0
    assert audit["suppression_counts"]["momentum_mail_blocked_daily_quality_below_threshold"] == 1
    # This producer confirmed a completed daily candle. A presentation-only
    # BEOBACHTEN action must not manufacture an intraday-unconfirmed pattern.
    assert "intraday_unconfirmed_pattern" not in audit["suppression_counts"]


def test_delivery_cooldown_blocks_mail_precheck_but_not_cached_chart_release(tmp_path):
    row = daily_row()
    key = api._alert_signal_identity_key("stock_strategy", row) + "_dailyclose"
    api._EMAIL_COOLDOWN[key] = NOW.timestamp() - 60
    state = api._scanner_result_trade_state("stock_strategy", row)
    assert state["alertable_now"] is True
    assert state["decision"] == "TRADE_NOW"
    assert "cooldown_active" not in state["display_reasons"]
    assert "cooldown_active" in mail_codes(state)
    assert state["mail_check"]["status"] == "blocked"
    audit = read_only_audit(tmp_path, row)
    assert audit["precheck_passed_count"] == 0
    assert audit["suppression_counts"]["cooldown_active"] == 1


@pytest.mark.parametrize("quality_score,status,released", [
    (70, "CONTINUATION_WATCH", False), (96, "CONTINUATION_OK", True),
])
def test_actual_visibility_keeps_confirmed_breakout_retest_warning_separate_from_admission(quality_score, status, released):
    row = daily_row(Breakout_Continuation_Score=quality_score, Breakout_Continuation_Status=status)
    api._apply_scanner_result_trade_state(row, "stock_strategy")
    visible = api._apply_scanner_visibility_policy("stock_strategy", [row])
    assert len(visible) == 1
    assert visible[0]["visibility_is_trade_signal"] is released
    assert visible[0]["visibility_status"] == ("released" if released else "candidate_warning")
    assert "breakout_confirmed_retest_pending" in [item["code"] for item in visible[0]["visibility_warnings"]]
    assert visible[0]["mail_check"]["status"] == ("checks_passed" if released else "blocked")
    if not released:
        assert "momentum_mail_blocked_daily_quality_below_threshold" in visible[0]["scanner_suppression_reasons"]


def test_read_only_cached_selection_neither_claims_nor_sends_nor_records(monkeypatch, tmp_path):
    def forbidden(*a, **k):
        pytest.fail("cached selection crossed a mutation or transport boundary")

    for name in ("_send_email_alert", "_send_strategy_scan_alerts", "_email_dedupe_claim",
                 "_email_dedupe_mark", "_email_dedupe_release", "_record_email_event",
                 "_safe_record_alert_signals", "_record_suppression_counts", "rate_limited_get",
                 "_fetch_recent_stock_5m_bars", "_revalidate_stock_strategy_mail_candidate"):
        monkeypatch.setattr(api, name, forbidden)
    row = daily_row()
    source = deepcopy(row)
    cooldowns = deepcopy(api._EMAIL_COOLDOWN)
    assert api._scanner_result_trade_state("stock_strategy", row)["mail_check"]["status"] == "checks_passed"
    assert read_only_audit(tmp_path, row)["precheck_passed_count"] == 1
    assert row == source and api._EMAIL_COOLDOWN == cooldowns
    assert not (tmp_path / "dedupe.json").exists()
