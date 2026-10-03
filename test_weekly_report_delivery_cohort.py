"""Offline report-origin scope and disjoint evidence-count contracts."""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from modules import signal_tracker as st


AS_OF = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
RECIPIENT_KEY = "d" * 64
SCANNER = "crypto_strategy"


def _row(ticker="ACCEPTED", *, ago=6, **overrides):
    accepted = AS_OF - timedelta(days=ago)
    row = {
        "created_at": (accepted - timedelta(hours=1)).isoformat(),
        "scanner": SCANNER, "strategy": "alpha", "ticker": ticker,
        "asset_class": "crypto", "direction": "LONG", "mail_class": "trade",
        "entry": 100.0, "stop": 95.0, "tp1": 105.0, "tp2": 110.0,
        "status": st.STATUS_STOP, "r_realized": -1.0,
        "entry_filled_at": (accepted + timedelta(minutes=1)).isoformat(),
        "entry_fill_price": 100.0,
        "closed_at": (accepted + timedelta(hours=1)).isoformat(),
        "stop_hit_at": (accepted + timedelta(hours=1)).isoformat(),
        "max_favorable_r": 0.0,
        "origin_evidence": "smtp_acceptance", "delivery_state": "ACTIVE",
        "delivery_accepted_at": accepted.isoformat(),
        "delivery_prepared_at": (accepted - timedelta(hours=1)).isoformat(),
        "delivery_attempted_at": (accepted - timedelta(minutes=1)).isoformat(),
        "delivery_recipient_keys_json": json.dumps([RECIPIENT_KEY]),
    }
    # Derive the exact immutable recorder identity, not a plausible-looking ref.
    candidate = st._deferred_delivery_candidates(
        SCANNER, [row], "weekly-" + ticker, "crypto"
    )[0]
    row.update({
        "setup_key": candidate["fields"]["setup_key"],
        "trade_horizon": candidate["fields"]["trade_horizon"],
        "delivery_intent_key": candidate["delivery_intent_key"],
        "public_signal_ref": candidate["public_signal_ref"],
    })
    row.update(overrides)
    return row


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", str(tmp_path / "report.sqlite"))
    monkeypatch.setattr(
        st, "SIGNAL_DELIVERY_JOURNAL_DB_PATH", str(tmp_path / "acceptance.sqlite")
    )
    return st


def _insert(tracker, rows):
    for row in rows:
        intent = "weekly-" + row["ticker"]
        prepared_at = datetime.fromisoformat(row["delivery_prepared_at"])
        with patch.object(tracker, "_utc_now", return_value=prepared_at):
            prepared = tracker.prepare_alert_delivery_intent(
                SCANNER, [row], intent, delivery_recipient_keys=[RECIPIENT_KEY]
            )
        assert prepared["send_allowed"] is True
        assert tracker.mark_alert_delivery_attempted(
            intent, attempted_at=row["delivery_attempted_at"]
        )["claimed"] is True
        accepted = tracker.finalize_alert_delivery(
            intent, [RECIPIENT_KEY], accepted_at=row["delivery_accepted_at"]
        )
        assert accepted["activated"] is True
        # Only fixtures are altered to exercise historical corruptions/outcomes.
        with tracker._db_connection() as conn:
            columns = list(row)
            conn.execute(
                "UPDATE signals SET %s WHERE id=?" % ",".join(
                    column + "=?" for column in columns
                ),
                [*[row[column] for column in columns], accepted["signal_ids"][0]],
            )


@pytest.mark.parametrize("bad_evidence", [
    {"origin_evidence": None},
    {"origin_evidence": "legacy_origin_unknown"},
    {"origin_evidence": "direct_post_send"},
    {"origin_evidence": "delivery_prepared"},
    {"public_signal_ref": "AS1-invalid"},
    {"delivery_accepted_at": None},
    {"delivery_accepted_at": "not-a-date"},
    {"delivery_state": None},
    {"delivery_state": "ACCEPTED_PENDING"},
    {"delivery_intent_key": None},
    {"delivery_intent_key": "different:row-bad"},
    {"entry": 101.0},
    {"stop": 110.0},
    {"ticker": "TAMPERED"},
    {"created_at": "not-a-date"},
    {"created_at": "2027-01-01T00:00:00+00:00"},
    {"delivery_prepared_at": None},
    {"delivery_prepared_at": "not-a-date"},
    {"delivery_prepared_at": "2027-01-01T00:00:00+00:00"},
    {"delivery_attempted_at": "not-a-date"},
    {"delivery_attempted_at": "2027-01-01T00:00:00+00:00"},
    {"delivery_recipient_keys_json": None},
    {"delivery_recipient_keys_json": "[]"},
    {"delivery_recipient_keys_json": "not-json"},
    {"delivery_recipient_keys_json": '{"key":"%s"}' % RECIPIENT_KEY},
    {"delivery_recipient_keys_json": json.dumps([RECIPIENT_KEY, "bad"])},
])
def test_strict_cohort_never_invents_smtp_evidence(bad_evidence):
    candidate = _row()
    candidate.update(bad_evidence)
    rows, counts = st.select_performance_cohort(
        [candidate], days=7, as_of=AS_OF,
        mature_only=False, require_delivery_evidence=True,
    )
    assert rows == []
    assert counts["excluded_delivery_evidence"] == 1
    assert counts["excluded_delivery_unknown_origin"] + counts[
        "excluded_delivery_invalid_acceptance"
    ] == 1


def test_strict_activity_uses_accepted_time_and_keeps_general_mode_compatible():
    accepted = _row(ago=2, created_at="2025-01-01T00:00:00+00:00")
    direct = _row("DIRECT", ago=2, origin_evidence="direct_post_send")
    shadow = _row("SHADOW", mail_class="shadow")
    pending = _row("PENDING", status=st.STATUS_PENDING_DELIVERY)
    candidates = [accepted, direct, shadow, pending]
    strict, counts = st.select_performance_cohort(
        candidates, days=7, as_of=AS_OF, mature_only=False,
        require_delivery_evidence=True,
    )
    general, _ = st.select_performance_cohort(
        candidates, days=7, as_of=AS_OF, mature_only=False
    )
    assert [row["ticker"] for row in strict] == ["ACCEPTED"]
    assert [row["ticker"] for row in general] == ["ACCEPTED", "DIRECT"]
    assert counts["created_in_window"] == 1
    assert counts["excluded_delivery_unknown_origin"] == 1


def test_exclusions_are_scoped_to_maturity_window_not_query_lookback():
    candidates = [
        _row("MATURE", ago=9),
        _row("FRESH", ago=2),
        _row("UNKNOWN_MATURE", ago=9, origin_evidence=None),
        _row("UNKNOWN_OLD", ago=30, origin_evidence=None),
        _row("UNKNOWN_FRESH", ago=2, origin_evidence=None),
    ]
    rows, counts = st.select_performance_cohort(
        candidates, days=7, as_of=AS_OF, mature_only=True,
        require_delivery_evidence=True,
    )
    assert [row["ticker"] for row in rows] == ["MATURE"]
    assert counts["excluded_delivery_evidence"] == 1
    assert counts["excluded_delivery_unknown_origin"] == 1
    assert counts["excluded_not_mature"] == 1


def test_strict_summary_reconciles_status_and_evidence_without_private_cohorts(tracker):
    _insert(tracker, [
        _row("RESOLVED"),
        _row("BE_UNRESOLVED", max_favorable_r=1.0,
             be_trigger_at=(AS_OF - timedelta(days=6, minutes=-3)).isoformat(),
             be_activated_at=(AS_OF - timedelta(days=6, minutes=-3)).isoformat()),
        _row("NO_FILL", status=st.STATUS_NO_FILL, entry_filled_at=None,
             entry_fill_price=None, r_realized=None,
             outcome_detail="entry_window_expired_no_fill"),
        _row("OPEN", status=st.STATUS_OPEN, entry_filled_at=None,
             entry_fill_price=None, r_realized=None, closed_at=None),
        _row("UNTRACKED", status=st.STATUS_UNTRACKED, entry_filled_at=None,
             entry_fill_price=None, r_realized=None),
        _row("BAD_TERMINAL", entry_filled_at=(AS_OF - timedelta(days=7)).isoformat()),
        _row("OLD_ENTRY_STOP", outcome_detail="ambiguous_same_day_entry_stop_and_tp1",
             entry_filled_at="2026-09-27", stop_hit_at="2026-09-27",
             fill_evidence_mode="daily_bar_touch", stop_gap_slippage_r=0.6),
        _row("UNKNOWN", origin_evidence=None),
        _row("INVALID", delivery_recipient_keys_json='["invalid"]'),
    ])
    with tracker._db_connection() as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM signals ORDER BY id")]
    summary = tracker.load_performance_summary(
        days=7, mature_only=True, as_of=AS_OF, require_delivery_evidence=True
    )
    bucket = summary["total"]
    expected_counts = {
        "resolved": 1, "evidence_unresolved": 3, "no_fill": 1,
        "still_open": 1, "untracked": 1, "report_total": 7,
        "reconciled": True,
    }
    assert bucket["signals"] == 7
    assert bucket["report_counts"] == expected_counts
    assert bucket["report_untracked_fill_claims"] == 0
    assert sum(bucket["report_counts"][key] for key in (
        "resolved", "evidence_unresolved", "no_fill", "still_open", "untracked"
    )) == bucket["signals"]
    assert bucket["decided_signals"] == 2  # valid Level-R, not the bad terminal/path
    assert bucket["managed_be_decided_signals"] == 1
    assert bucket["control_no_fill"] == 1
    assert bucket["control_unresolved"] == 2
    assert bucket["stop_gap_exits"] == 0
    assert bucket["sample_reliable"] is False
    assert summary["delivery_evidence"]["candidate_signals"] == 9
    assert summary["delivery_evidence"]["excluded_unknown_origin"] == 1
    assert summary["delivery_evidence"]["excluded_invalid_acceptance"] == 1
    assert summary["cohort"]["excluded_delivery_evidence"] == 2
    assert summary["cohort"]["accepted_in_window"] == 7
    assert summary["per_scanner"][SCANNER]["report_counts"] == expected_counts
    assert summary["per_strategy"]["alpha"]["report_counts"] == expected_counts
    assert summary["segments"][0]["report_counts"]["reconciled"] is True
    serialized = json.dumps(summary)
    assert RECIPIENT_KEY not in serialized
    assert "delivery_recipient_keys_json" not in serialized
    with tracker._db_connection() as conn:
        after = [tuple(row) for row in conn.execute("SELECT * FROM signals ORDER BY id")]
    assert after == before
    assert summary["source_read_complete"] is True
    assert summary["report_data_available"] is True
    assert summary["error"] is None


def test_summary_default_preserves_descriptive_history_and_strict_activity_filters(tracker):
    _insert(tracker, [
        _row("ACCEPTED", ago=2, created_at="2025-01-01T00:00:00+00:00"),
        _row("DIRECT", ago=2, origin_evidence="direct_post_send"),
        _row("LEGACY", ago=2, origin_evidence=None),
    ])
    general = tracker.load_performance_summary(days=7, as_of=AS_OF)
    strict = tracker.load_performance_summary(
        days=7, as_of=AS_OF, require_delivery_evidence=True
    )
    assert general["total"]["signals"] == 3
    assert strict["total"]["signals"] == 1
    assert strict["total"]["alerts_per_day"] == round(1 / 7, 3)
    assert strict["cohort_selection_basis"] == "accepted_in_window"
    assert strict["delivery_evidence"]["excluded_unknown_origin"] == 2


def test_proven_fill_lower_loss_is_retained_but_missing_upper_is_report_unresolved(tracker):
    _insert(tracker, [_row(
        "FILLED_AMBIGUOUS", outcome_detail="ambiguous_same_day_stop_and_tp1",
        fill_evidence_mode="verified_snapshot", r_realized_upper=None,
    )])
    summary = tracker.load_performance_summary(
        days=7, mature_only=True, as_of=AS_OF, require_delivery_evidence=True
    )
    bucket = summary["total"]
    assert bucket["decided_signals"] == 1
    assert bucket["sum_r"] == -1.0
    assert bucket["upper_unresolved"] == 1
    assert bucket["report_counts"] == {
        "resolved": 0, "evidence_unresolved": 1, "no_fill": 0,
        "still_open": 0, "untracked": 0, "report_total": 1, "reconciled": True,
    }


def test_untracked_fill_claim_is_visible_without_double_counting(tracker):
    _insert(tracker, [_row("FILLED_UNTRACKED", status=st.STATUS_UNTRACKED, r_realized=None)])
    summary = tracker.load_performance_summary(
        days=7, mature_only=True, as_of=AS_OF, require_delivery_evidence=True
    )
    bucket = summary["total"]
    assert bucket["control_unresolved"] == 1
    assert bucket["report_untracked_fill_claims"] == 1
    assert bucket["report_counts"] == {
        "resolved": 0, "evidence_unresolved": 0, "no_fill": 0,
        "still_open": 0, "untracked": 1, "report_total": 1, "reconciled": True,
    }


def test_loader_counts_malformed_acceptance_in_its_creation_diagnostic_window(tracker):
    _insert(tracker, [_row()])
    with tracker._db_connection() as conn:
        conn.execute("UPDATE signals SET delivery_accepted_at='malformed'")
    summary = tracker.load_performance_summary(
        days=7, mature_only=True, as_of=AS_OF, require_delivery_evidence=True
    )
    assert summary["report_data_available"] is True
    assert summary["total"]["signals"] == 0
    assert summary["delivery_evidence"]["excluded_invalid_acceptance"] == 1
    assert summary["delivery_evidence"]["candidate_signals"] == 1


@pytest.mark.parametrize("failure_phase", ["database_read", "aggregation"])
def test_strict_summary_failure_never_returns_success_shaped_empty_or_partial_report(
    tracker, monkeypatch, failure_phase
):
    _insert(tracker, [_row()])
    secret_message = "private-recipient@example.invalid SECRET database path"
    if failure_phase == "database_read":
        @contextmanager
        def broken_connection():
            raise OSError(secret_message)
            yield  # pragma: no cover
        monkeypatch.setattr(tracker, "_db_connection", broken_connection)
    else:
        def broken_counts(*args, **kwargs):
            raise ValueError(secret_message)
        monkeypatch.setattr(tracker, "_add_report_counts", broken_counts)
    summary = tracker.load_performance_summary(
        days=7, mature_only=True, as_of=AS_OF, require_delivery_evidence=True
    )
    assert summary["report_data_available"] is False
    assert summary["source_read_complete"] is (failure_phase == "aggregation")
    assert summary["error"].startswith("report_data_unavailable:")
    assert summary["total"]["signals"] == 0
    assert summary["per_scanner"] == {}
    assert summary["recent"] == []
    assert summary["cohort"]["included_signals"] == 0
    assert secret_message not in json.dumps(summary)
