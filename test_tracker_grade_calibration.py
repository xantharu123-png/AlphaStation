#!/usr/bin/env python3
"""Task-5 contract: grade calibration is separate, strict reporting only."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import modules.signal_tracker as st
import modules.regime_filter as regime_filter


AS_OF = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
CREATED = AS_OF - timedelta(days=45)


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", str(tmp_path / "grade.sqlite"))
    monkeypatch.setattr(
        st,
        "SIGNAL_DELIVERY_JOURNAL_DB_PATH",
        str(tmp_path / "grade_delivery.sqlite"),
    )
    return st


def _row(ticker, *, grade="A", direction="LONG", regime="GREEN"):
    if direction == "SHORT":
        stop, tp1, tp2 = 105.0, 95.0, 90.0
    else:
        stop, tp1, tp2 = 95.0, 105.0, 110.0
    return {
        "Ticker": ticker,
        "Signal_Direction": direction,
        "Entry": 100.0,
        "StopLoss": stop,
        "TP1": tp1,
        "TP2": tp2,
        "Preis": 100.0,
        "grade": grade,
        "trade_horizon": "swing",
        "evaluation_horizon_bars": 5,
        "market_regime": regime,
    }


def _record_resolved_sample(tracker, *, grade="A", prefix="A", count=30):
    rows = [_row(f"{prefix}-{index:02d}", grade=grade) for index in range(count)]
    assert tracker.record_alert_signals("breakout", rows, mail_class="trade") == count
    entry_at = (CREATED + timedelta(hours=1)).isoformat()
    tp1_at = (CREATED + timedelta(days=2)).isoformat()
    tp2_at = (CREATED + timedelta(days=3)).isoformat()
    delivered_at = CREATED + timedelta(days=2, minutes=1)
    winner_ids = []
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET created_at=?, entry_filled_at=?, "
            "entry_fill_price=entry, closed_at=?, status=CASE "
            "WHEN CAST(substr(ticker, -2) AS INTEGER) < 15 THEN 'TP2_HIT' "
            "ELSE 'STOP_HIT' END, r_realized=CASE "
            "WHEN CAST(substr(ticker, -2) AS INTEGER) < 15 THEN 2.0 "
            "ELSE -1.0 END, tp1_hit_at=CASE "
            "WHEN CAST(substr(ticker, -2) AS INTEGER) < 15 THEN ? ELSE NULL END, "
            "tp2_hit_at=CASE "
            "WHEN CAST(substr(ticker, -2) AS INTEGER) < 15 THEN ? ELSE NULL END, "
            "max_favorable_r=CASE "
            "WHEN CAST(substr(ticker, -2) AS INTEGER) < 15 THEN 2.0 ELSE 0.0 END, "
            "be_trigger_at=CASE "
            "WHEN CAST(substr(ticker, -2) AS INTEGER) < 15 THEN ? ELSE NULL END, "
            "be_activated_at=CASE "
            "WHEN CAST(substr(ticker, -2) AS INTEGER) < 15 THEN ? ELSE NULL END "
            "WHERE ticker LIKE ?",
            (
                CREATED.isoformat(), entry_at, tp2_at, tp1_at, tp2_at,
                tp1_at, tp1_at, f"{prefix}-%",
            ),
        )
        conn.commit()
        winner_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM signals WHERE ticker LIKE ? "
                "AND be_activated_at IS NOT NULL ORDER BY id",
                (f"{prefix}-%",),
            ).fetchall()
        ]
    finally:
        conn.close()
    for signal_id in winner_ids:
        receipt_id = tracker.record_followup_delivery_receipt(
            signal_id,
            event_kind="BE",
            delivery_evidence_key=f"signal_be_{signal_id}_recipient_delivered",
            accepted_at=delivered_at,
        )
        assert receipt_id
        assert tracker.mark_be_alerts_sent(
            [signal_id], delivery_receipt_ids={signal_id: receipt_id}
        ) == 1


def _grade_cells(tracker):
    summary = tracker.load_performance_summary(
        days=90, mature_only=True, as_of=AS_OF
    )
    return summary, summary["grade_calibration_cells"]


def _record_mature_control_extras(tracker, tickers):
    rows = [_row(ticker, grade="A") for ticker in tickers]
    assert tracker.record_alert_signals("breakout", rows, mail_class="trade") == len(rows)
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET created_at=?, closed_at=? "
            "WHERE ticker IN (%s)"
            % ",".join("?" for _ticker in tickers),
            (
                CREATED.isoformat(),
                (CREATED + timedelta(days=3)).isoformat(),
                *tickers,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _record_corrupt_terminal_control_population(tracker):
    corrupt = {
        "BAD-STOP-NO-FILL-AT": ("STOP_HIT", "missing_fill_at"),
        "BAD-TP2-FILL-AT": ("TP2_HIT", "invalid_fill_at"),
        "BAD-EXP-NO-FILL-PRICE": ("EXPIRED", "missing_fill_price"),
        "BAD-STOP-FILL-PRICE": ("STOP_HIT", "invalid_fill_price"),
        "BAD-TP2-NO-R": ("TP2_HIT", "missing_r"),
        "BAD-EXP-R": ("EXPIRED", "invalid_r"),
    }
    tickers = [*corrupt, "PROVEN-NO-FILL"]
    _record_mature_control_extras(tracker, tickers)
    fill_at = (CREATED + timedelta(hours=1)).isoformat()
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        for ticker, (status, _corruption) in corrupt.items():
            conn.execute(
                "UPDATE signals SET status=?, entry_filled_at=?, "
                "entry_fill_price=entry, r_realized=-1.0 WHERE ticker=?",
                (status, fill_at, ticker),
            )
        conn.execute(
            "UPDATE signals SET entry_filled_at=NULL "
            "WHERE ticker='BAD-STOP-NO-FILL-AT'"
        )
        conn.execute(
            "UPDATE signals SET entry_filled_at='not-a-time' "
            "WHERE ticker='BAD-TP2-FILL-AT'"
        )
        conn.execute(
            "UPDATE signals SET entry_fill_price=NULL "
            "WHERE ticker='BAD-EXP-NO-FILL-PRICE'"
        )
        conn.execute(
            "UPDATE signals SET entry_fill_price=0 "
            "WHERE ticker='BAD-STOP-FILL-PRICE'"
        )
        conn.execute(
            "UPDATE signals SET r_realized=NULL WHERE ticker='BAD-TP2-NO-R'"
        )
        conn.execute(
            "UPDATE signals SET r_realized='not-a-number' "
            "WHERE ticker='BAD-EXP-R'"
        )
        conn.execute(
            "UPDATE signals SET status='NO_FILL', entry_filled_at=NULL, "
            "entry_fill_price=NULL, r_realized=NULL, "
            "outcome_detail='entry_not_filled_within_window' "
            "WHERE ticker='PROVEN-NO-FILL'"
        )
        conn.commit()
    finally:
        conn.close()


def test_grade_calibration_has_five_dimensions_and_managed_payoff_metrics(tracker):
    _record_resolved_sample(tracker)

    summary, cells = _grade_cells(tracker)

    assert summary["grade_calibration_dimensions"] == [
        "scanner", "grade", "direction", "horizon", "market_regime",
    ]
    assert len(cells) == 1
    cell = cells[0]
    assert cell["cell_id"] == "breakout|A|LONG|swing:5bars|GREEN"
    assert cell["scanner"] == "breakout"
    assert cell["grade"] == "A"
    assert cell["direction"] == "LONG"
    assert cell["horizon"] == "swing:5bars"
    assert cell["market_regime"] == "GREEN"
    assert cell["eligible_signals"] == 30
    assert cell["n"] == 30
    assert cell["wins"] == 15
    assert cell["losses"] == 15
    assert cell["breakevens"] == 0
    assert cell["hit_rate_pct"] == pytest.approx(50.0)
    assert cell["win_rate_wilson_95"]["lower_pct"] < 50.0
    assert cell["win_rate_wilson_95"]["upper_pct"] > 50.0
    assert cell["avg_r"] == pytest.approx(0.25)
    assert cell["sum_r"] == pytest.approx(7.5)
    assert cell["profit_factor"] == pytest.approx(1.5)
    assert cell["unresolved"] == 0
    assert cell["sample_reliable"] is True
    assert cell["reporting_only"] is True
    assert "verdict" not in cell


def test_grade_cells_split_grade_without_changing_legacy_four_dimensional_cell(tracker):
    _record_resolved_sample(tracker, grade="A", prefix="A")
    _record_resolved_sample(tracker, grade="B", prefix="B", count=1)

    summary, cells = _grade_cells(tracker)

    assert {cell["grade"] for cell in cells} == {"A", "B"}
    assert len(summary["calibration_cells"]) == 1
    legacy = summary["calibration_cells"][0]
    assert legacy["cell_id"] == "breakout|LONG|swing:5bars|GREEN"
    assert "grade" not in legacy
    recovery = tracker.load_breaker_recovery_summary(
        "breakout",
        AS_OF - timedelta(days=60),
        direction="LONG",
        horizon="swing:5bars",
        market_regime="GREEN",
        as_of=AS_OF,
    )
    assert recovery["cell_id"] == "breakout|LONG|swing:5bars|GREEN"
    assert recovery["available"] is True
    assert recovery["decided"] == 31


def test_grade_calibration_excludes_unqualified_and_fresh_but_keeps_corrupt_terminal(tracker):
    _record_resolved_sample(tracker)
    extras = [
        _row("LEGACY", grade="S"),
        _row("PREPARED", grade="S"),
        _row("SHADOW", grade="S"),
        _row("NO-FILL", grade="S"),
        _row("FRESH", grade="S"),
    ]
    assert tracker.record_alert_signals("breakout", extras[:2], mail_class="trade") == 2
    assert tracker.record_alert_signals("breakout", extras[2:3], mail_class="shadow") == 1
    assert tracker.record_alert_signals("breakout", extras[3:], mail_class="trade") == 2
    mature = CREATED.isoformat()
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, "
            "created_at=?, entry_filled_at=?, entry_fill_price=entry, closed_at=? "
            "WHERE ticker IN ('LEGACY','PREPARED','SHADOW')",
            (mature, mature, mature),
        )
        conn.execute("UPDATE signals SET origin_evidence=NULL WHERE ticker='LEGACY'")
        conn.execute(
            "UPDATE signals SET origin_evidence='delivery_prepared' "
            "WHERE ticker='PREPARED'"
        )
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, created_at=?, "
            "entry_filled_at=NULL, entry_fill_price=NULL WHERE ticker='NO-FILL'",
            (mature,),
        )
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, "
            "entry_filled_at=?, entry_fill_price=entry WHERE ticker='FRESH'",
            (AS_OF.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()

    _summary, cells = _grade_cells(tracker)

    by_grade = {cell["grade"]: cell for cell in cells}
    assert set(by_grade) == {"A", "S"}
    assert by_grade["A"]["eligible_signals"] == 30
    assert by_grade["S"]["eligible_signals"] == 1
    assert by_grade["S"]["n"] == 0
    assert by_grade["S"]["unresolved"] == 1
    assert by_grade["S"]["terminal_r_unresolved"] == 1
    assert by_grade["S"]["sample_reliable"] is False


def test_grade_calibration_accepts_only_complete_smtp_acceptance_evidence(tracker):
    rows = [_row("SMTP-GOOD", grade="S"), _row("SMTP-BAD", grade="S")]
    assert tracker.record_alert_signals("breakout", rows, mail_class="trade") == 2
    mature = CREATED.isoformat()
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, "
            "created_at=?, entry_filled_at=?, entry_fill_price=entry, closed_at=?, "
            "origin_evidence='smtp_acceptance', delivery_accepted_at=? "
            "WHERE ticker IN ('SMTP-GOOD','SMTP-BAD')",
            (mature, mature, mature, mature),
        )
        conn.execute(
            "UPDATE signals SET public_signal_ref='AS1-0123456789ABCDEF0123' "
            "WHERE ticker='SMTP-GOOD'"
        )
        conn.execute(
            "UPDATE signals SET public_signal_ref=NULL WHERE ticker='SMTP-BAD'"
        )
        conn.commit()
    finally:
        conn.close()

    _summary, cells = _grade_cells(tracker)

    assert len(cells) == 1
    assert cells[0]["grade"] == "S"
    assert cells[0]["eligible_signals"] == 1
    assert cells[0]["n"] == 1
    assert cells[0]["sample_reliable"] is False


def test_unresolved_managed_be_row_blocks_reliability_without_changing_metrics(tracker):
    _record_resolved_sample(tracker)
    assert tracker.record_alert_signals(
        "breakout", [_row("UNRESOLVED", grade="A")], mail_class="trade"
    ) == 1
    entry_at = (CREATED + timedelta(hours=1)).isoformat()
    tp1_at = (CREATED + timedelta(days=2)).isoformat()
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET created_at=?, entry_filled_at=?, "
            "entry_fill_price=entry, closed_at=?, status='STOP_HIT', "
            "r_realized=-1.0, tp1_hit_at=?, max_favorable_r=1.2, "
            "be_trigger_at=?, be_activated_at=?, be_mail_sent_at=NULL "
            "WHERE ticker='UNRESOLVED'",
            (
                CREATED.isoformat(), entry_at,
                (CREATED + timedelta(days=3)).isoformat(), tp1_at, tp1_at, tp1_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _summary, cells = _grade_cells(tracker)
    cell = cells[0]

    assert cell["eligible_signals"] == 31
    assert cell["n"] == 30
    assert cell["unresolved"] == 1
    assert cell["sample_reliable"] is False
    assert cell["avg_r"] == pytest.approx(0.25)
    assert cell["sum_r"] == pytest.approx(7.5)


def test_mature_filled_untracked_blocks_performance_and_weekly_verdict(tracker):
    _record_resolved_sample(tracker)
    _record_mature_control_extras(tracker, ["FILLED-UNTRACKED"])
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='UNTRACKED', entry_filled_at=?, "
            "entry_fill_price=entry, r_realized=NULL, "
            "outcome_detail='evaluation_failed_after_confirmed_fill' "
            "WHERE ticker='FILLED-UNTRACKED'",
            ((CREATED + timedelta(hours=1)).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()

    summary, cells = _grade_cells(tracker)
    bucket = summary["total"]

    assert bucket["decided_signals"] == 30
    assert bucket["control_eligible_signals"] == 31
    assert bucket["control_resolved_signals"] == 30
    assert bucket["control_unresolved"] == 1
    assert bucket["terminal_r_unresolved"] == 1
    assert bucket["control_no_fill"] == 0
    assert bucket["control_eligible_signals"] == (
        bucket["control_resolved_signals"]
        + bucket["control_unresolved"]
        + bucket["control_no_fill"]
    )
    assert bucket["sample_reliable"] is False
    assert bucket["managed_be_sample_reliable"] is False
    verdict, reason = tracker.scanner_verdict(bucket)
    assert verdict == "beobachten"
    assert "Terminal-R" in reason
    assert cells[0]["eligible_signals"] == 31
    assert cells[0]["n"] == 30
    assert cells[0]["terminal_r_unresolved"] == 1
    assert cells[0]["sample_reliable"] is False


def test_corrupt_terminal_outcomes_and_proven_no_fill_preserve_population(tracker):
    _record_resolved_sample(tracker)
    _record_corrupt_terminal_control_population(tracker)

    summary, cells = _grade_cells(tracker)
    bucket = summary["total"]
    cell = cells[0]

    assert bucket["control_eligible_signals"] == 37
    assert bucket["control_resolved_signals"] == 30
    assert bucket["control_unresolved"] == 6
    assert bucket["terminal_r_unresolved"] == 6
    assert bucket["control_no_fill"] == 1
    assert bucket["control_eligible_signals"] == (
        bucket["control_resolved_signals"]
        + bucket["control_unresolved"]
        + bucket["control_no_fill"]
    )
    assert bucket["sample_reliable"] is False
    assert cell["eligible_signals"] == 37
    assert cell["n"] == 30
    assert cell["unresolved"] == 6
    assert cell["terminal_r_unresolved"] == 6
    assert cell["no_fill"] == 1
    assert cell["eligible_signals"] == (
        cell["n"] + cell["unresolved"] + cell["no_fill"]
    )
    assert cell["sample_reliable"] is False


def test_breaker_recovery_blocks_mature_terminal_r_unresolved_population(tracker):
    _record_resolved_sample(tracker)
    _record_corrupt_terminal_control_population(tracker)

    recovery = tracker.load_breaker_recovery_summary(
        "breakout",
        AS_OF - timedelta(days=60),
        direction="LONG",
        horizon="swing:5bars",
        market_regime="GREEN",
        as_of=AS_OF,
    )

    assert recovery["fully_observed_post_trip"] == 37
    assert recovery["eligible_signals"] == 37
    assert recovery["decided"] == 30
    assert recovery["control_no_fill"] == 1
    assert recovery["control_unresolved"] == 6
    assert recovery["terminal_r_unresolved"] == 6
    assert recovery["eligible_signals"] == (
        recovery["decided"]
        + recovery["control_unresolved"]
        + recovery["control_no_fill"]
    )
    assert recovery["available"] is False
    assert recovery["error"] == "terminal_r_unresolved"


def test_legacy_origin_rows_cannot_form_a_breaker_calibration_cell(tracker):
    """Descriptive legacy history must never become breaker control evidence."""
    _record_resolved_sample(tracker, prefix="LEGACY-ONLY")
    with sqlite3.connect(st.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET origin_evidence=NULL, status='STOP_HIT', "
            "r_realized=-1.0, r_realized_upper=-1.0, tp1_hit_at=NULL, "
            "tp2_hit_at=NULL, be_trigger_at=NULL, be_activated_at=NULL, "
            "be_mail_sent_at=NULL, be_delivery_evidence_key=NULL, "
            "max_favorable_r=0.0 WHERE ticker LIKE 'LEGACY-ONLY-%'"
        )
        conn.commit()

    summary = tracker.load_performance_summary(
        days=90, mature_only=True, as_of=AS_OF
    )

    # The rows remain visible as descriptive history, but their unknown origin
    # cannot create a calibration cell or satisfy the breaker denominator.
    assert summary["total"]["decided_signals"] == 30
    assert summary["total"]["control_eligible_signals"] == 0
    assert summary["calibration_cells"] == []

    identity = {
        "cell_id": "breakout|LONG|swing:5bars|GREEN",
        "scanner": "breakout",
        "direction": "LONG",
        "horizon": "swing:5bars",
        "market_regime": "GREEN",
        "trip_release_eligible": True,
    }
    metrics = regime_filter.breaker_metrics(
        summary, "breakout", calibration_cell=identity
    )
    assert metrics["joint_cell_verified"] is False
    assert metrics["decided"] == 0
    assert regime_filter.evaluate_breaker(metrics, None, now=AS_OF)["state"] == (
        regime_filter.GREEN
    )


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_breaker_fails_closed_when_joint_cell_has_unresolved_ohlc_upper_path(
    tracker, direction
):
    prefix = f"AMB-{direction}"
    rows = [
        _row(f"{prefix}-{index:02d}", direction=direction, regime="RISK_ON")
        for index in range(30)
    ]
    assert tracker.record_alert_signals("breakout", rows, mail_class="trade") == 30
    fill_at = (CREATED + timedelta(hours=1)).isoformat()
    closed_at = (CREATED + timedelta(days=3)).isoformat()
    detail = "ambiguous_same_day_stop_and_tp1"
    with sqlite3.connect(st.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET created_at=?, entry_filled_at=?, "
            "entry_fill_price=entry, closed_at=?, stop_hit_at=?, "
            "status='STOP_HIT', r_realized=-1.0, r_realized_upper=1.0, "
            "outcome_detail=?, max_favorable_r=1.2 WHERE ticker LIKE ?",
            (CREATED.isoformat(), fill_at, closed_at, closed_at, detail, f"{prefix}-%"),
        )
        conn.commit()

    summary, grade_cells = _grade_cells(tracker)
    bucket = summary["calibration_cells"][0]
    grade_cell = grade_cells[0]
    assert bucket["ambiguous_outcomes"] == 30
    assert bucket["upper_unresolved"] == 30
    assert bucket["avg_r"] == pytest.approx(-1.0)
    assert bucket["avg_r_upper"] == pytest.approx(-1.0)
    assert bucket["avg_r_managed_50_50_be_upper"] == pytest.approx(-1.0)
    assert grade_cell["ambiguous_outcomes"] == 30
    assert grade_cell["upper_unresolved"] == 30
    assert grade_cell["sample_reliable"] is False

    identity = {
        key: bucket[key]
        for key in ("cell_id", "scanner", "direction", "horizon", "market_regime")
    }
    identity["trip_release_eligible"] = True
    metrics = regime_filter.breaker_metrics(
        summary, "breakout", calibration_cell=identity
    )
    decision = regime_filter.evaluate_breaker(metrics, None, now=AS_OF)
    assert metrics["upper_unresolved"] == 30
    assert metrics["trip_release_eligible"] is False
    assert decision["state"] == regime_filter.GREEN
    assert decision["metrics"]["avg_r_upper"] == pytest.approx(-1.0)


def test_legacy_optimistic_ambiguous_upper_is_quarantined_from_breaker_and_grade(
    tracker,
):
    _record_resolved_sample(tracker)
    assert tracker.record_alert_signals(
        "breakout", [_row("LEGACY-AMB", grade="A")], mail_class="trade"
    ) == 1
    fill_at = (CREATED + timedelta(hours=1)).isoformat()
    closed_at = (CREATED + timedelta(days=3)).isoformat()
    with sqlite3.connect(st.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET created_at=?, entry_filled_at=?, "
            "entry_fill_price=entry, closed_at=?, stop_hit_at=?, "
            "status='STOP_HIT', r_realized=-1.0, r_realized_upper=1.0, "
            "outcome_detail='ambiguous_same_day', max_favorable_r=1.2 "
            "WHERE ticker='LEGACY-AMB'",
            (CREATED.isoformat(), fill_at, closed_at, closed_at),
        )
        conn.commit()

    summary, cells = _grade_cells(tracker)
    bucket = summary["total"]
    cell = cells[0]
    assert bucket["control_eligible_signals"] == 31
    assert bucket["control_resolved_signals"] == 30
    assert bucket["control_unresolved"] == 1
    assert bucket["ambiguity_unresolved"] == 1
    assert cell["eligible_signals"] == 31
    assert cell["n"] == 30
    assert cell["unresolved"] == 1
    assert cell["ambiguity_unresolved"] == 1
    assert cell["sample_reliable"] is False

    recovery = tracker.load_breaker_recovery_summary(
        "breakout",
        AS_OF - timedelta(days=60),
        direction="LONG",
        horizon="swing:5bars",
        market_regime="GREEN",
        as_of=AS_OF,
    )
    assert recovery["eligible_signals"] == 31
    assert recovery["decided"] == 30
    assert recovery["control_unresolved"] == 1
    assert recovery["ambiguity_unresolved"] == 1
    assert recovery["available"] is False
    assert recovery["error"] == "ambiguity_unresolved"


def test_legacy_be_timestamp_without_recipient_evidence_blocks_grade_and_breaker(
    tracker,
):
    rows = [_row(f"CLEAN-{index:02d}", grade="A") for index in range(30)]
    rows.append(_row("LEGACY-BE-ACK", grade="A"))
    assert tracker.record_alert_signals("breakout", rows, mail_class="trade") == 31
    fill_at = (CREATED + timedelta(hours=1)).isoformat()
    trigger_at = (CREATED + timedelta(days=1)).isoformat()
    closed_at = (CREATED + timedelta(days=3)).isoformat()
    with sqlite3.connect(st.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET created_at=?, entry_filled_at=?, "
            "entry_fill_price=entry, closed_at=?, stop_hit_at=?, "
            "status='STOP_HIT', r_realized=-1.0, max_favorable_r=0.0 "
            "WHERE ticker LIKE 'CLEAN-%'",
            (CREATED.isoformat(), fill_at, closed_at, closed_at),
        )
        conn.execute(
            "UPDATE signals SET created_at=?, entry_filled_at=?, "
            "entry_fill_price=entry, closed_at=?, tp1_hit_at=?, tp2_hit_at=?, "
            "status='TP2_HIT', r_realized=2.0, r_realized_be=2.0, "
            "max_favorable_r=2.0, "
            "be_trigger_at=?, be_activated_at=?, be_mail_sent_at=? "
            "WHERE ticker='LEGACY-BE-ACK'",
            (
                CREATED.isoformat(), fill_at, closed_at, trigger_at, closed_at,
                trigger_at, trigger_at, (CREATED + timedelta(days=1, minutes=1)).isoformat(),
            ),
        )
        conn.commit()

    summary, cells = _grade_cells(tracker)
    bucket = summary["total"]
    cell = cells[0]
    assert bucket["managed_be_decided_signals"] == 30
    assert bucket["managed_be_unresolved"] == 1
    assert bucket["managed_be_sample_reliable"] is False
    assert cell["eligible_signals"] == 31
    assert cell["n"] == 30
    assert cell["managed_be_unresolved"] == 1
    assert cell["sample_reliable"] is False
    legacy_recent = next(
        row for row in summary["recent"] if row["ticker"] == "LEGACY-BE-ACK"
    )
    assert legacy_recent["r_realized_be"] is None
    assert legacy_recent["be_unresolved"] is True

    recovery = tracker.load_breaker_recovery_summary(
        "breakout",
        AS_OF - timedelta(days=60),
        direction="LONG",
        horizon="swing:5bars",
        market_regime="GREEN",
        as_of=AS_OF,
    )
    assert recovery["eligible_signals"] == 31
    assert recovery["decided"] == 30
    assert recovery["managed_be_unresolved"] == 1
    assert recovery["available"] is False
    assert recovery["error"] == "managed_be_unresolved"
