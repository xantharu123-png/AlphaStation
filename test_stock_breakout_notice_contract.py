"""Offline boundaries for confirmed stock observations without a historical TP1.

The base row is produced by the actual OHLCV scanner in an isolated subprocess.
Admission, price/structure validators, rendering and durable dedupe stay real;
only external account/data boundaries and the mail wire are replaced.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

import api
from modules import email_dedupe as store
from modules import regime_filter
from modules import stock_swing_contract as swing
from modules.trade_levels import TP1_KEYS, TP2_KEYS


NOW = datetime(2026, 9, 17, 21, tzinfo=timezone.utc)
NOW_TS = NOW.timestamp()


@pytest.fixture(scope="module")
def native_row(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-breakout-notice")
    evidence = _native_probe(directory, "no_target")
    assert evidence["structure_status"] == "ACCEPT", evidence
    assert evidence["suppression_reasons"] == ["trade_target_not_structural"], evidence
    assert evidence["alertable_now"] is False, evidence
    assert evidence["persisted_deliveries"] == [], evidence
    return evidence["row"]


def _native_probe(directory, scenario):
    script = Path(__file__).with_name("test_stock_native_plan_mail_integration.py")
    result = subprocess.run(
        [sys.executable, "-B", str(script), "LONG", str(directory), scenario],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines()
                if line.startswith("NATIVE_PLAN_RESULT="))
    return json.loads(line.split("=", 1)[1])


@pytest.fixture(scope="module")
def native_trade_row(tmp_path_factory):
    evidence = _native_probe(tmp_path_factory.mktemp("native-released-trade"), "breakout_close")
    assert evidence["structure_status"] == "ACCEPT", evidence
    assert evidence["suppression_reasons"] == [], evidence
    assert evidence["alertable_now"] is True, evidence
    assert evidence["final"] == {"ok": True}, evidence
    assert evidence["persisted_deliveries"] == [["trade", "email", "stocks_swing", "ACTIVE", 0]], evidence
    return evidence["row"]


@pytest.fixture(scope="module")
def native_gap_row(tmp_path_factory):
    evidence = _native_probe(tmp_path_factory.mktemp("native-gap-observation"), "gap_no_target")
    assert evidence["structure_status"] == "ACCEPT", evidence
    assert evidence["suppression_reasons"] == ["trade_target_not_structural"], evidence
    assert evidence["alertable_now"] is False, evidence
    assert evidence["persisted_deliveries"] == [], evidence
    assert evidence["row"]["Strategy"] == "Gap Momentum Long", evidence
    return evidence["row"]


@pytest.fixture(autouse=True)
def isolated_notice_boundaries(monkeypatch, tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(swing, "datetime", Clock)
    monkeypatch.setattr(api.time, "time", lambda: NOW_TS)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_mail_outbox", None)
    monkeypatch.setattr(api, "_load_common_stock_universe_cached",
                        lambda **_: ({"TEST"}, "offline-fixture"))
    monkeypatch.setattr(api, "_load_common_stock_universe",
                        lambda **_: ({"TEST"}, "offline-fixture"))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})
    def forbidden(*args, **kwargs):
        pytest.fail("Breakout notice must not fetch live data or use real SMTP")
    monkeypatch.setattr(api, "rate_limited_get", forbidden)
    monkeypatch.setattr(api.req.sessions.Session, "request", forbidden)
    monkeypatch.setattr(api.smtplib, "SMTP", forbidden)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", forbidden)
    api._set_last_delivery_outcome("not_attempted")


def notice(row, state=None):
    helper = getattr(api, "_stock_breakout_notice_evidence", None)
    assert callable(helper), "Confirmed breakout notice admission is not implemented"
    return helper(row, NOW_TS, state=state)


def gap_row(native_row):
    row = deepcopy(native_row)
    row.update(Strategy="Gap Momentum Long", strategy="Gap Momentum Long",
               Momentum_Breakout_Type="GAP_UP_MOMENTUM")
    return row


def _wire(monkeypatch, *, result=True, outcome="accepted", exception=None):
    messages = []
    def transport(subject, body, **kwargs):
        messages.append({"subject": subject, "body": body, **kwargs})
        api._set_last_delivery_outcome(outcome)
        if exception is not None:
            raise exception
        return result
    monkeypatch.setattr(api, "_send_email_alert", transport)
    return messages


def _moving_clock(monkeypatch):
    state = {"now": NOW}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            current = state["now"]
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(swing, "datetime", Clock)
    monkeypatch.setattr(api.time, "time", lambda: state["now"].timestamp())
    return state


def _reticker(native, ticker):
    row = deepcopy(native)
    row.update(Ticker=ticker, ticker=ticker, Business_Data_Status="fixture_complete")
    for name in ("Level_Structure", "level_structure"):
        if isinstance(row.get(name), dict):
            row[name]["symbol"] = ticker
    return row


def _mixed_scan_context(monkeypatch, tmp_path, tickers):
    # Real scoring/classification/revalidation and claim ownership remain live.
    # The company universe and empty regime cache are offline source fixtures.
    monkeypatch.setattr(api, "_load_common_stock_universe_cached",
                        lambda **_: (set(tickers), "offline-fixture"))
    monkeypatch.setattr(api, "_load_common_stock_universe",
                        lambda **_: (set(tickers), "offline-fixture"))
    monkeypatch.setattr(regime_filter, "DEFAULT_STATE_PATH", tmp_path / "regime.json")
    monkeypatch.setattr(api, "_ALERT_EMAIL_MAX_ROWS", 10)


def test_native_projection_breakout_is_an_observation_not_trade_admission(native_row):
    row = deepcopy(native_row)
    before = deepcopy(row)
    proof = notice(row)
    assert proof is not None
    assert proof["ticker"] == "TEST"
    assert proof["strategy"] == "Momentum Breakout Long"
    assert proof["direction"] == "LONG"
    assert proof["session"] == "2026-09-17"
    assert proof["close"] == 107.5
    assert 0 < proof["level"] < proof["close"]
    assert row == before
    assert api._alert_trade_plan_ok(row) is False


def test_first_breakout_notice_is_not_sent_on_the_next_new_york_date(native_row, monkeypatch):
    """Latest daily Swing evidence alone must not resurrect yesterday's breakout."""
    clock = _moving_clock(monkeypatch)
    clock["now"] = datetime(2026, 9, 18, 10, tzinfo=timezone.utc)
    assert swing.validate(native_row, clock["now"]) is True
    messages = _wire(monkeypatch)
    assert api._send_stock_breakout_notices([deepcopy(native_row)], max_rows=1) == 0
    assert messages == []
    assert api._EMAIL_COOLDOWN == {}


def test_combined_guard_does_not_mail_old_breakout_as_new(native_row, monkeypatch, tmp_path):
    clock = _moving_clock(monkeypatch)
    clock["now"] = datetime(2026, 9, 18, 10, tzinfo=timezone.utc)
    _mixed_scan_context(monkeypatch, tmp_path, {"TEST"})
    messages = _wire(monkeypatch)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [deepcopy(native_row)], "stocks")
    assert messages == []
    assert api._EMAIL_COOLDOWN == {}


def test_rendering_across_new_york_midnight_does_not_send_still_swing_valid_notice(native_row, monkeypatch):
    clock = _moving_clock(monkeypatch)
    clock["now"] = datetime(2026, 9, 18, 3, 59, 59, tzinfo=timezone.utc)
    messages = _wire(monkeypatch)
    render = api._stock_breakout_notice_html
    def cross_midnight(*args, **kwargs):
        html = render(*args, **kwargs)
        clock["now"] = datetime(2026, 9, 18, 4, tzinfo=timezone.utc)
        return html
    monkeypatch.setattr(api, "_stock_breakout_notice_html", cross_midnight)
    assert api._send_stock_breakout_notices([deepcopy(native_row)], max_rows=1) == 0
    assert swing.validate(native_row, clock["now"]) is True
    assert messages == []
    assert api._EMAIL_COOLDOWN == {}


@pytest.mark.parametrize("mutation", [
    "weak_target", "structural_target", "reject", "missing_structure_reason",
    "missing_decision", "contradictory_decision", "row_barrier", "nested_barrier",
    "barrier_gate", "projected_stop", "invalid_stop", "nonboolean_projection",
])
def test_plan_defects_do_not_masquerade_as_missing_historical_target(native_row, mutation):
    row = deepcopy(native_row)
    setup = row["trade_setup"]
    if mutation == "weak_target":
        setup["target_quality"] = row["target_quality"] = "WEAK_STRUCTURAL_TARGETS"
    elif mutation == "structural_target":
        setup["tp1_is_projection"] = False
    elif mutation == "reject":
        setup["structure_status"] = row["structure_status"] = "REJECT"
    elif mutation == "missing_structure_reason":
        setup.pop("structure_reason", None)
    elif mutation == "missing_decision":
        setup.pop("structure_decision", None)
    elif mutation == "contradictory_decision":
        setup["structure_decision"]["reason"] = "causal_structure_missing"
    elif mutation in {"row_barrier", "nested_barrier"}:
        (row if mutation == "row_barrier" else setup)["nearest_barrier"] = {
            "price": 108., "side": "resistance", "zone_id": "independent-wall"}
    elif mutation == "barrier_gate":
        setup["barrier_gate"] = "BREAK_RECLAIM_REQUIRED"
    elif mutation == "projected_stop":
        setup["stop_is_projection"] = True
    elif mutation == "invalid_stop":
        setup["stop"] = row["StopLoss"] = row["stop_loss"] = row["Entry"]
    elif mutation == "nonboolean_projection":
        setup["tp1_is_projection"] = 1
    assert notice(row) is None


@pytest.mark.parametrize("field,value", [
    ("stock_swing_contract_version", True), ("stock_swing_contract_version", 0),
    ("scan_price_source", "polygon_snapshot_last_trade"),
    ("scan_price_observed_at", "2026-09-18T09:59:59Z"),
    ("swing_analysis_session", "2026-09-16"), ("swing_timeframe", "5m"),
    ("swing_data_delay_seconds", 0), ("fill_evidence_verified", True),
    ("swing_reference_close", float("nan")),
    ("Breakout_Confirmation_Close", 102.),
    ("Breakout_Confirmation_Timeframe", "5m"),
    ("Breakout_Freshness_Status", "NOT_CONFIRMED"),
    ("Momentum_Execution_Confirmed", True),
    ("Momentum_Contract_Version", True),
    ("current_price", 99.),
])
def test_unconfirmed_or_tampered_completed_daily_contract_is_not_notice(native_row, field, value):
    row = deepcopy(native_row)
    row[field] = value
    assert notice(row) is None


@pytest.mark.parametrize("strategy", ["Unknown Breakout", "BI Scanner LONG", "crypto_strategy", "Elliott Waves"])
def test_generic_momentum_validator_cannot_admit_unapproved_strategy(native_row, strategy):
    row = deepcopy(native_row)
    row.update(Strategy=strategy, strategy=strategy)
    assert notice(row) is None


@pytest.mark.parametrize("extra_reason", [
    "score_below_alert_threshold", "grade_below_alert_threshold",
    "rvol_below_alert_threshold", "trade_structure_not_confirmed",
    "trade_health_no_trade", "trade_health_fakeout_risk", "non_common_stock_product",
])
def test_only_the_missing_target_blocker_is_bypassed(native_row, extra_reason):
    state = {"suppression_reasons": ["trade_target_not_structural", extra_reason]}
    assert notice(deepcopy(native_row), state=state) is None


def test_regular_trade_cooldowns_do_not_change_notice_identity(native_row):
    row = deepcopy(native_row)
    baseline = notice(row)
    state = {"suppression_reasons": ["trade_target_not_structural", "cooldown_active", "persistent_dedupe_active"]}
    assert notice(row, state=state) == baseline


def test_gap_uses_saved_closed_zone_proof_not_presentation_warning(native_row):
    row = gap_row(native_row)
    assert notice(row) is not None
    for snapshot_name in ("Level_Structure", "level_structure"):
        if isinstance(row.get(snapshot_name), dict):
            for zone in row[snapshot_name]["zones"]:
                zone.pop("break_reclaim_evidence", None)
    assert row.get("breakout_confirmation") == "confirmed_close"
    assert notice(row) is None


@pytest.mark.parametrize("mutation", [
    "wrong_origin", "missing_origin", "wrong_symbol", "wrong_model",
    "wrong_direction", "missing_prior_confirmation", "future_proof", "wrong_boundary",
    "wrong_asset", "wrong_horizon", "wrong_reference_price", "contradictory_alias",
])
def test_gap_zone_proof_must_bind_original_structure_direction_and_session(native_row, mutation):
    row = gap_row(native_row)
    for snapshot_name in ("Level_Structure", "level_structure"):
        snapshot = row.get(snapshot_name)
        if not isinstance(snapshot, dict):
            continue
        if mutation == "wrong_symbol":
            snapshot["symbol"] = "FOREIGN"
        if mutation == "wrong_model":
            snapshot["model"] = "unknown_structure"
        if mutation == "wrong_asset":
            snapshot["asset_class"] = "crypto"
        if mutation == "wrong_horizon":
            snapshot["horizon"] = "intraday"
        if mutation == "wrong_reference_price":
            snapshot["current_price"] = 10.
        if mutation == "contradictory_alias" and snapshot_name == "level_structure":
            snapshot["symbol"] = "FOREIGN"
        for zone in snapshot["zones"]:
            proof = zone.get("break_reclaim_evidence")
            if mutation == "wrong_origin":
                zone["origin_roles"] = ["support"]
            elif mutation == "missing_origin":
                zone.pop("origin_roles", None)
            if not isinstance(proof, dict):
                continue
            if mutation == "wrong_direction":
                proof["direction"] = "SHORT"
            elif mutation == "missing_prior_confirmation":
                zone.pop("confirmed_at", None)
            elif mutation == "future_proof":
                proof["as_of"] = "2026-09-18T20:00:00Z"
            elif mutation == "wrong_boundary":
                proof["boundary"] = 1.
    assert notice(row) is None


@pytest.mark.parametrize("field,value", [
    ("score", 0), ("grade", "D"), ("RVOL", .1), ("Business_Severe_Risk", True),
])
def test_native_other_quality_failures_remain_blocked_without_supplied_state(native_row, field, value):
    row = deepcopy(native_row)
    row[field] = value
    if field == "RVOL":
        row["rvol"] = value
    assert notice(row) is None


@pytest.mark.parametrize("target_shape", ["tiny_reward", "targets_too_close", "overdominant_runner"])
@pytest.mark.parametrize("use_scan_state", [False, True])
def test_projection_does_not_hide_independent_target_rr_quality_failure(native_row, target_shape, use_scan_state):
    row = deepcopy(native_row)
    # The optional state is real classifier output from the preceding scan,
    # not a fabricated admission. Targets must still be checked on this row.
    scan_state = api._classify_alert_candidate("stock_strategy", row, NOW_TS)
    assert scan_state["suppression_reasons"] == ["trade_target_not_structural"]
    entry = row["Entry"]
    risk = entry - row["StopLoss"]
    target_r = {
        "tiny_reward": (.01, .02),
        "targets_too_close": (1.6, 1.61),
        "overdominant_runner": (1.6, 20.),
    }[target_shape]
    tp1, tp2 = [entry + risk * multiple for multiple in target_r]
    for keys, value in ((TP1_KEYS, tp1), (TP2_KEYS, tp2)):
        for key in keys:
            if key in row:
                row[key] = value
    row.update(TP1=tp1, tp1=tp1, TP2=tp2, tp2=tp2)
    row["trade_setup"].update(tp1=tp1, tp2=tp2)
    levels = api._alert_trade_levels(row)
    quality = api._alert_trade_plan_quality(levels)
    assert levels["valid"] is True  # Ordered prices do not prove usable targets.
    assert quality["issues"], quality
    if target_shape == "tiny_reward":
        assert quality["tp1_ok"] is False
        assert quality["effective_rr"] < api._ALERT_MIN_LEVEL_RR
    assert notice(row, state=scan_state if use_scan_state else None) is None


def test_notice_renderer_escapes_descriptive_content_and_does_not_publish_plan(native_row):
    row = deepcopy(native_row)
    proof = notice(row)
    proof.update(ticker='<script>alert("ticker")</script>',
                 strategy='<img src=x onerror="unsafe">', direction='LONG & "watch"')
    row["trade_setup"].update(entry=12345.67, stop=23456.78, tp1=34567.89, tp2=45678.9)
    body = api._stock_breakout_notice_html(row, proof)
    assert "<script" not in body and "<img" not in body
    assert "&lt;script&gt;" in body and "&lt;img" in body
    assert "LONG &amp; &quot;watch&quot;" in body
    assert "kein freigegebener Handelsplan" in body
    for plan_claim in ("Entry", "Stop", "TP1", "TP2", "R:R", "12345.67", "23456.78", "34567.89", "45678.9"):
        assert plan_claim not in body


def test_notice_renderer_does_not_promote_presentation_metadata_to_retest_proof(native_row):
    # The native closed-break producer emits not_confirmed, not the unrelated
    # Wyckoff trigger_mode enum confirmed_retest.
    assert native_row["retest_status"] == "not_confirmed"
    assert native_row["trade_setup"]["retest_status"] == "not_confirmed"
    body = api._stock_breakout_notice_html(native_row, notice(native_row))
    assert "Rückteststatus im Chart prüfen." in body
    assert "Rücktest bestätigt." not in body
    assert "Rücktest noch offen." not in body


def test_sender_uses_watch_swing_without_any_tracker_contract_and_is_once_per_notice(native_row, monkeypatch):
    row = deepcopy(native_row)
    key = notice(row)["cooldown_key"]
    regular = api._alert_signal_identity_key("stock_strategy", row) + "_dailyclose"
    api._EMAIL_COOLDOWN[regular] = NOW_TS - 1
    api._email_dedupe_mark(regular, now=NOW_TS - 1)
    messages = _wire(monkeypatch)
    assert api._send_stock_breakout_notices([row], max_rows=1) == 1
    assert len(messages) == 1
    assert messages[0]["mail_class"] == "watch"
    assert messages[0]["mail_channel"] == "stocks_swing"
    assert messages[0]["trade_horizon"] == "swing"
    assert messages[0]["delivery_dedupe_keys"] == [key]
    for forbidden_field in ("tracking_scanner", "tracking_rows", "tracking_scope"):
        assert not messages[0].get(forbidden_field)
    stored = store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=NOW_TS)
    assert stored[key] == NOW_TS
    assert stored[regular] == NOW_TS - 1
    assert api._EMAIL_COOLDOWN[regular] == NOW_TS - 1
    assert api._send_stock_breakout_notices([row], max_rows=1) == 0
    assert len(messages) == 1


@pytest.mark.parametrize("outcome", ["failed", "refused", "not_attempted"])
def test_definite_nonacceptance_releases_only_notice_claim_and_allows_retry(native_row, monkeypatch, outcome):
    row = deepcopy(native_row)
    key = notice(row)["cooldown_key"]
    messages = _wire(monkeypatch, result=False, outcome=outcome)
    assert api._send_stock_breakout_notices([row], max_rows=1) == 1
    assert key not in api._EMAIL_COOLDOWN
    assert store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=NOW_TS) == {}
    assert api._send_stock_breakout_notices([row], max_rows=1) == 1
    assert len(messages) == 2


@pytest.mark.parametrize("outcome", ["unknown", "partial_unknown", "accepted_unjournaled"])
def test_uncertain_acceptance_does_not_release_claim_or_retry(native_row, monkeypatch, outcome):
    row = deepcopy(native_row)
    key = notice(row)["cooldown_key"]
    messages = _wire(monkeypatch, result=False, outcome=outcome)
    assert api._send_stock_breakout_notices([row], max_rows=1) == 1
    assert key not in api._EMAIL_COOLDOWN
    stored = store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=NOW_TS)
    assert stored["__delivery_claim__:" + key] == NOW_TS
    assert api._send_stock_breakout_notices([row], max_rows=1) == 0
    assert len(messages) == 1


def test_sender_exception_preserves_unknown_delivery_ownership(native_row, monkeypatch):
    row = deepcopy(native_row)
    key = notice(row)["cooldown_key"]
    _wire(monkeypatch, outcome="unknown", exception=RuntimeError("offline uncertain DATA"))
    assert api._send_stock_breakout_notices([row], max_rows=1) == 1
    assert store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=NOW_TS)["__delivery_claim__:" + key] == NOW_TS


def test_zero_notice_quota_never_calls_transport_or_claims(native_row, monkeypatch):
    messages = _wire(monkeypatch)
    assert api._send_stock_breakout_notices([deepcopy(native_row)], max_rows=0) == 0
    assert messages == []
    assert store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=NOW_TS) == {}


def test_no_eligible_watch_recipients_does_not_consume_notice_or_attempt_smtp(native_row, monkeypatch):
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "operator@example.invalid", "GMAIL_APP_PASSWORD": "synthetic-only",
        "ALERT_EMAIL": "operator@example.invalid", "ALERT_OPERATOR_WATCH_OPTIN": "0",
    })
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)
    monkeypatch.setattr(api, "mail_channel_enabled", lambda *a, **k: True)
    monkeypatch.setattr(api, "_EMAIL_STARTUP_TIME", NOW_TS - 1000)
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])
    row = deepcopy(native_row)
    key = notice(row)["cooldown_key"]
    api._send_stock_breakout_notices([row], max_rows=1)
    assert any(event["reason"] == "watch_no_eligible_recipients" for event in api._EMAIL_SEND_LOG)
    assert key not in api._EMAIL_COOLDOWN
    assert store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=NOW_TS) == {}


def test_rendering_across_daily_session_releases_claim_without_mail(native_row, monkeypatch):
    row = deepcopy(native_row)
    proof = notice(row)
    clock = _moving_clock(monkeypatch)
    render = api._stock_breakout_notice_html
    renders = []
    def crossing_renderer(item, evidence):
        assert swing.validate(item, clock["now"]) is True
        renders.append(render(item, evidence))
        # Sep 18 has become the latest completed 1D session after the 15m delay.
        clock["now"] = datetime(2026, 9, 18, 20, 16, tzinfo=timezone.utc)
        assert swing.validate(item, clock["now"]) is False
        return renders[-1]
    monkeypatch.setattr(api, "_stock_breakout_notice_html", crossing_renderer)
    messages = _wire(monkeypatch)
    assert api._send_stock_breakout_notices([row], max_rows=1) == 0
    assert len(renders) == 1
    assert messages == []
    assert proof["cooldown_key"] not in api._EMAIL_COOLDOWN
    assert store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=clock["now"].timestamp()) == {}


def test_lost_delivery_lease_during_render_does_not_send_or_release_new_owner(native_row, monkeypatch):
    row = deepcopy(native_row)
    key = notice(row)["cooldown_key"]
    clock = _moving_clock(monkeypatch)
    render = api._stock_breakout_notice_html
    replaced_at = NOW_TS + 1000
    def stolen_lease_renderer(item, evidence):
        body = render(item, evidence)
        clock["now"] = datetime.fromtimestamp(replaced_at, timezone.utc)
        # Model an independently retired/replaced reservation using the real
        # owner-aware store, independent of the notice's retention duration.
        assert api._email_dedupe_release(key, claimed_at=NOW_TS) is True
        assert api._email_dedupe_claim(key, 7 * 86400, now=replaced_at) is True
        return body
    monkeypatch.setattr(api, "_stock_breakout_notice_html", stolen_lease_renderer)
    messages = _wire(monkeypatch)
    assert api._send_stock_breakout_notices([row], max_rows=1) == 0
    assert messages == []
    assert key not in api._EMAIL_COOLDOWN
    assert store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=replaced_at) == {
        "__delivery_claim__:" + key: replaced_at,
    }


@pytest.mark.parametrize("trade_count", [1, 8, 12])
@pytest.mark.parametrize("accepted", [True, False])
def test_mixed_scan_reserves_full_trade_priority_and_combines_attempt_budget(
        native_row, native_trade_row, monkeypatch, tmp_path, trade_count, accepted):
    observations = [_reticker(native_row, f"WATCH{i:02}") for i in range(12)]
    trades = [_reticker(native_trade_row, f"TRADE{i:02}") for i in range(trade_count)]
    _mixed_scan_context(monkeypatch, tmp_path, [row["ticker"] for row in observations + trades])
    # Keep the native classifier and every admission gate real, including final
    # daily-plan validation. A high-score observation listed first cannot steal
    # the slots reserved for released structural plans.
    assert all(notice(row) is not None for row in observations)
    assert all(api._classify_alert_candidate("stock_strategy", row, NOW_TS)["alertable_now"]
               for row in trades)
    messages = _wire(monkeypatch, result=accepted, outcome="accepted" if accepted else "unknown")
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", observations + trades, "stocks")
    trade_messages = [message for message in messages if message["mail_class"] == "swing_trade"]
    notice_messages = [message for message in messages if message["mail_class"] == "watch"]
    assert len(messages) == 10
    assert len(trade_messages) == min(trade_count, 10)
    assert len(notice_messages) == 10 - min(trade_count, 10)
    assert [message["mail_class"] for message in messages] == (
        ["swing_trade"] * min(trade_count, 10)
        + ["watch"] * (10 - min(trade_count, 10)))
    assert all(message["tracking_scanner"] == "stock_strategy" for message in trade_messages)
    assert all(not message.get("tracking_rows") for message in notice_messages)
    assert all(message["mail_channel"] == "stocks_swing" for message in messages)
    assert all(notice(row)["cooldown_key"] not in api._EMAIL_COOLDOWN
               for row in observations[len(notice_messages):])


def test_uncertain_notice_exception_does_not_abort_remaining_full_trade(
        native_row, native_trade_row, monkeypatch, tmp_path):
    observation = _reticker(native_row, "WATCH")
    trade = _reticker(native_trade_row, "TRADE")
    _mixed_scan_context(monkeypatch, tmp_path, {"WATCH", "TRADE"})
    notice_key = notice(observation)["cooldown_key"]
    calls = []
    def transport(subject, body, **kwargs):
        calls.append({"subject": subject, "body": body, **kwargs})
        if kwargs["mail_class"] == "watch":
            api._set_last_delivery_outcome("unknown")
            raise RuntimeError("offline uncertain notice DATA")
        api._set_last_delivery_outcome("accepted")
        return True
    monkeypatch.setattr(api, "_send_email_alert", transport)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [observation, trade], "stocks")
    assert [call["mail_class"] for call in calls] == ["swing_trade", "watch"]
    assert calls[0]["tracking_rows"][0]["ticker"] == "TRADE"
    stored = store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=NOW_TS)
    assert stored["__delivery_claim__:" + notice_key] == NOW_TS
    assert notice_key not in api._EMAIL_COOLDOWN
    assert stored[calls[0]["delivery_dedupe_keys"][0]] == NOW_TS


def test_uncertain_notice_is_not_retried_after_normal_trade_lease_and_ram_loss(native_row, monkeypatch):
    row = deepcopy(native_row)
    key = notice(row)["cooldown_key"]
    clock = _moving_clock(monkeypatch)
    messages = _wire(monkeypatch, result=False, outcome="unknown")
    assert api._send_stock_breakout_notices([row], max_rows=1) == 1
    clock["now"] = datetime.fromtimestamp(NOW_TS + 901, timezone.utc)
    api._EMAIL_COOLDOWN.clear()  # Process restart cannot erase uncertain DATA ownership.
    assert swing.validate(row, clock["now"]) is True
    assert api._send_stock_breakout_notices([row], max_rows=1) == 0
    assert len(messages) == 1
    stored = store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=clock["now"].timestamp())
    assert stored["__delivery_claim__:" + key] == NOW_TS
    assert key not in stored  # An unknown SMTP result is not a sent timestamp.


def test_notice_exception_consumes_attempt_and_continues_only_within_remaining_quota(
        native_row, monkeypatch):
    rows = [_reticker(native_row, f"WATCH{i}") for i in range(3)]
    monkeypatch.setattr(api, "_load_common_stock_universe_cached",
                        lambda **_: ({row["ticker"] for row in rows}, "offline-fixture"))
    keys = [notice(row)["cooldown_key"] for row in rows]
    calls = []
    def transport(subject, body, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            api._set_last_delivery_outcome("unknown")
            raise RuntimeError("offline uncertain first notice")
        api._set_last_delivery_outcome("accepted")
        return True
    monkeypatch.setattr(api, "_send_email_alert", transport)
    assert api._send_stock_breakout_notices(rows, max_rows=2) == 2
    assert [call["delivery_dedupe_keys"] for call in calls] == [[keys[0]], [keys[1]]]
    stored = store.load_email_dedupe(api._EMAIL_DEDUPE_FILE, now=NOW_TS)
    assert stored["__delivery_claim__:" + keys[0]] == NOW_TS
    assert stored[keys[1]] == NOW_TS
    assert keys[0] not in api._EMAIL_COOLDOWN
    assert keys[1] in api._EMAIL_COOLDOWN
    assert keys[2] not in stored and "__delivery_claim__:" + keys[2] not in stored


@pytest.mark.parametrize("field,invalid_value", [
    ("quality_flags", [{}]), ("quality_flags", True), ("zones", 1),
])
def test_malformed_native_gap_snapshot_does_not_abort_normal_trade_discovery(
        native_gap_row, native_trade_row, monkeypatch, tmp_path, field, invalid_value):
    observation = _reticker(native_gap_row, "BADGAP")
    trade = _reticker(native_trade_row, "TRADE")
    _mixed_scan_context(monkeypatch, tmp_path, {"BADGAP", "TRADE"})
    assert notice(observation) is not None
    for name in ("Level_Structure", "level_structure"):
        observation[name][field] = deepcopy(invalid_value)
    messages = _wire(monkeypatch)
    # The invalid Gap is deliberately inspected before its valid sibling.
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [observation, trade], "stocks")
    assert [message["mail_class"] for message in messages] == ["swing_trade"]
    assert messages[0]["tracking_rows"][0]["ticker"] == "TRADE"
    assert notice(observation) is None
