"""Quality-floor arithmetic must not imply a missing future retest."""
from copy import deepcopy
import pytest
import api
from modules import stock_swing_contract as swing


def quality(**changes):
    args = dict(strategy_name="Momentum Breakout Long", history_metrics={"high_20d":100.},
        score_meta={"upper_wick_pct":15., "atr_pct":3., "extension_atr":.67},
        breakout_type="20D_HIGH_BREAKOUT",price=101.,change_pct=2.,rvol=1.5,
        close_pos=.75,completed_daily=True)
    args.update(changes)
    return api._stock_momentum_breakout_continuation_quality(**args)


def daily_row(q, **changes):
    row=dict(Strategy="Momentum Breakout Long",direction="LONG",price=101.,ATR14=3.,
        RVOL=1.5,Close_Position=.75,Upper_Wick_Pct=15.,Change_Pct=2.,
        Breakout_Freshness_Checked=True,Breakout_Freshness_Status="DAILY_CONFIRMED",
        Momentum_Breakout_Type="20D_HIGH_BREAKOUT",Breakout_Continuation_Score=q["score"],
        Breakout_Continuation_Status=q["status"],Breakout_Fakeout_Risk=q["risk"])
    row.update(swing.metadata("2026-09-23",101.))
    row.update(changes)
    return row


def test_positive_descriptions_cannot_hide_quality_threshold_failure():
    q=quality()
    audit=q["quality_audit"]
    assert q["score"]==70 and q["status"]=="CONTINUATION_WATCH"
    assert len(q["reasons"])==4
    assert "70/96" in q["blockers"][0] and "78" in q["blockers"][0]
    assert audit["score_deficit"]==8 and not audit["meets_mail_quality_floor"]
    assert audit["evidence"]=="completed_daily" and audit["timeframe"]=="1D"
    assert audit["semantics"]=="daily_bar_quality_not_future_continuation_or_retest"
    assert audit["maximum_score"]==96 and audit["mail_min_score"]==78
    assert {c["code"] for c in audit["components"]}=={"close","wick","volume","level","timing"}
    for component in audit["components"]:
        assert component["shortfall_points"]==pytest.approx(component["max_points"]-component["points"],abs=.0001)
    assert round(sum(c["points"] for c in audit["components"])-sum(d["points"] for d in audit["deductions"]))==audit["score"]


def test_high_quality_is_not_full_mail_release_or_retest_confirmation():
    q=quality(score_meta={"upper_wick_pct":5.,"atr_pct":3.,"extension_atr":.67},close_pos=.95,rvol=3.)
    audit=q["quality_audit"]
    assert q["score"]==96 and q["status"]=="CONTINUATION_OK"
    assert audit["meets_mail_quality_floor"] and audit["score_deficit"]==0
    assert q["blockers"]==[]
    assert "alertable" not in audit and "retest_confirmed" not in audit


def test_gap_deductions_reconcile_without_changing_existing_score():
    q=quality(history_metrics={"ema20":5.,"high_20d":5.5},score_meta={"upper_wick_pct":42.,"atr_pct":4.,"extension_atr":1.5},breakout_type="TREND_RECLAIM",price=5.42,change_pct=8.3,rvol=4.56,close_pos=.61,gap_pct=7.6,open_to_current_pct=0.)
    audit=q["quality_audit"]
    assert {d["code"] for d in audit["deductions"]}=={"gap_reclaim","gap_open_not_held","gap_close_weak","blowoff_wick"}
    raw=sum(c["points"] for c in audit["components"])-sum(d["points"] for d in audit["deductions"])
    assert max(0,round(raw))==q["score"]


@pytest.mark.parametrize("value",[True,False,float("nan"),float("inf"),-float("inf"),None])
@pytest.mark.parametrize("field",["price","change_pct","rvol","close_pos"])
def test_unusable_input_has_no_invented_quality_breakdown(field,value):
    assert quality(**{field:value})=={}


def test_daily_rejection_names_quality_not_missing_future_continuation():
    row=daily_row(quality())
    before=deepcopy(row)
    assert api._stock_strategy_mail_quality_state(row,daily_close_confirmed_mode=True)==(False,"momentum_mail_blocked_daily_quality_below_threshold")
    assert row==before


def test_current_session_keeps_legacy_reason_and_explicit_evidence_type():
    q=quality(completed_daily=False)
    assert q["quality_audit"]["evidence"]=="current_session"
    row=daily_row(q)
    row.pop("stock_swing_mode")
    assert api._stock_strategy_mail_quality_state(row)==(False,"momentum_mail_blocked_breakout_continuation_watch")


def test_daily_chase_reason_is_daily_not_intraday_requirement():
    q=quality(score_meta={"upper_wick_pct":5.,"atr_pct":8.,"extension_atr":1.},close_pos=.95,rvol=3.)
    row=daily_row(q,Momentum_Breakout_Type="10D_HIGH_BREAKOUT",Change_Pct=8.,Close_Position=.95,Upper_Wick_Pct=5.,RVOL=3.)
    assert api._stock_strategy_mail_quality_state(row,daily_close_confirmed_mode=True)==(False,"momentum_mail_blocked_daily_move_extended")


@pytest.mark.parametrize("value",[None,True,float("nan"),float("inf")])
def test_daily_missing_quality_cannot_gain_mail_permission(value):
    row=daily_row(quality(),Breakout_Continuation_Score=value,Breakout_Continuation_Status="CONTINUATION_OK")
    assert api._stock_strategy_mail_quality_state(row,daily_close_confirmed_mode=True)==(False,"momentum_mail_blocked_daily_quality_unavailable")


@pytest.mark.parametrize("field",["upper_wick_pct","extension_atr","atr_pct"])
@pytest.mark.parametrize("value",[True,float("nan"),float("inf"),None])
def test_invalid_component_evidence_has_no_invented_breakdown(field,value):
    meta={"upper_wick_pct":15.,"atr_pct":3.,"extension_atr":.67,field:value}
    assert quality(score_meta=meta)=={}


@pytest.mark.parametrize("score,expected",[(77,False),(78,True),(96,True)])
def test_existing_daily_quality_floor_is_unchanged(score,expected):
    row=daily_row(quality(),Breakout_Continuation_Score=score,Breakout_Continuation_Status="CONTINUATION_OK")
    assert api._stock_strategy_mail_quality_state(row,daily_close_confirmed_mode=True)[0] is expected
    assert "momentum_quality" not in row  # No invented component history for legacy rows.


def test_daily_missing_status_and_extreme_number_fail_closed():
    for changes in ({"Breakout_Continuation_Status":None},{"Breakout_Continuation_Score":10**400}):
        row=daily_row(quality(),**changes)
        assert api._stock_strategy_mail_quality_state(row,daily_close_confirmed_mode=True)==(False,"momentum_mail_blocked_daily_quality_unavailable")


def test_daily_score_passing_but_status_not_confirmed_is_not_score_shortfall():
    row=daily_row(quality(),Breakout_Continuation_Score=80,Breakout_Continuation_Status="WICK_WATCH")
    assert api._stock_strategy_mail_quality_state(row,daily_close_confirmed_mode=True)==(False,"momentum_mail_blocked_daily_quality_unconfirmed")


def test_wrapper_publishes_exact_daily_quality_without_fetching_live_confirmation(monkeypatch):
    from test_stock_momentum_confirmed_contract import _wrapper_fixture,NOW,NAME
    from test_stock_starter_swing import payload
    written=_wrapper_fixture(monkeypatch)
    session=swing.completed_sessions(NOW,1)[0]
    data=payload(session,102.)
    data["results"][0].update(o=98.,h=102.,l=96.)
    feed=swing.universe(swing.parse_grouped(data,session),{"TEST":{"c":98.,"v":1_000_000}},session)
    monkeypatch.setattr(api,"_fetch_strategy_snapshot_universe",lambda *a:feed)
    monkeypatch.setattr(api,"_fetch_recent_stock_5m_bars",lambda *a,**k:pytest.fail("no live confirmation in daily mode"))
    rows=api._strategy_scan_wrapper(NAME,send_email=False)
    assert len(rows)==1 and written
    audit=rows[0]["momentum_quality"]
    assert audit["score"]==rows[0]["Breakout_Continuation_Score"]
    assert audit["evidence"]=="completed_daily"
    assert audit["maximum_score"]==96 and audit["mail_min_score"]==78


def test_daily_codes_are_persisted_without_dynamic_diagnostic_text(tmp_path):
    import json
    from modules import suppression_telemetry as telemetry
    codes={"momentum_mail_blocked_"+suffix for suffix in (
        "daily_quality_below_threshold","daily_quality_unavailable","daily_quality_unconfirmed",
        "daily_move_extended","daily_target_previously_touched")}
    assert codes<=telemetry.ALLOWED_SUPPRESSION_REASONS
    assert codes<=set(api._ALERT_SUPPRESSION_LABELS)
    db=str(tmp_path/"suppression.sqlite")
    counts={code:1 for code in codes}
    counts["private_customer@example.invalid price 123.45"]=10
    assert telemetry.record_suppressions("stock_strategy",counts,observed_at=1800000000.,db_path=db)==5
    summary=telemetry.load_suppression_summary(now=1800000001.,db_path=db)
    assert {row["reason"] for row in summary["top_reasons"]}==codes
    assert "private_customer" not in json.dumps(summary)
