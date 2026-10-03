"""Offline first-entry/stop ordering regressions for Daily and interval paths."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from modules import signal_tracker as st


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
LEGACY_DETAILS = (
    "ambiguous_same_day_entry_and_stop_unresolved_upper",
    "ambiguous_same_day_entry_stop_and_tp1",
    "ambiguous_same_day_entry_stop_and_tp2",
)


def _row(direction="LONG", **overrides):
    sign = 1 if direction == "LONG" else -1
    row = {
        "ticker": "CAUSAL-ENTRY", "scanner": "bi_long" if sign == 1 else "bi_short",
        "mail_class": "trade", "origin_evidence": "direct_post_send", "direction": direction,
        "entry": 100.0, "stop": 100 - sign * 5, "tp1": 100 + sign * 10,
        "tp2": 100 + sign * 20, "status": st.STATUS_OPEN,
        "created_at": "2026-08-24T14:00:00+00:00", "evaluation_horizon_bars": 20,
        "entry_filled_at": None, "entry_fill_price": None, "tp1_hit_at": None,
        "tp2_hit_at": None, "stop_hit_at": None, "r_realized": None,
        "max_favorable_r": 0.0, "max_adverse_r": 0.0,
    }
    row.update(overrides)
    return row


def _bar(direction, *, opening=98, high=102, low=94, close=99, day="2026-08-25"):
    if direction == "SHORT":
        opening, high, low, close = 200 - opening, 200 - low, 200 - high, 200 - close
    return {"date": day, "open": opening, "high": high, "low": low, "close": close}


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("target", ["none", "tp1", "tp2"])
def test_daily_first_entry_stop_has_neither_fill_nor_realized_loss(direction, target):
    # A stop-first path invalidates the setup; an entry-first path loses.
    # Even a later target cannot distinguish those paths from Daily OHLC.
    row = _row(direction, max_favorable_r=8.0, max_adverse_r=-3.0,
               mae_evidence_mode="intrabar_fill_order_unresolved",
               be_trigger_at="2026-08-24T18:00:00+00:00")
    before = deepcopy(row)
    bar = _bar(direction, high={"none": 102, "tp1": 112, "tp2": 122}[target])
    updates, failed = st._evaluate_stock_signal(row, lambda *_: [bar], NOW)

    assert not failed
    assert row == before
    assert updates["status"] == st.STATUS_UNTRACKED
    assert updates["outcome_detail"] == "ambiguous_entry_and_stop_same_interval"
    assert updates["closed_at"] == NOW.isoformat()
    assert updates["last_eval_at"] == NOW.isoformat()
    assert updates["evaluation_model_version"] == st.EVALUATION_MODEL_VERSION
    for field in (
        "entry_filled_at", "entry_fill_price", "tp1_hit_at", "tp2_hit_at", "stop_hit_at",
        "r_realized", "r_realized_upper", "r_realized_be", "exit_fill_price",
        "stop_gap_slippage_r", "stop_gap_slippage_pct", "be_trigger_at", "be_exit_at",
        "be_exit_fill_price", "be_exit_evidence_mode", "mae_evidence_mode",
    ):
        assert updates[field] is None, field
    assert updates["max_favorable_r"] == updates["max_adverse_r"] == 0.0
    merged = dict(row, **updates)
    assert st._control_population_resolution(merged, NOW, require_mature=False) == "unresolved"
    assert st._managed_5050_be_resolution(merged) == (None, True)


@pytest.mark.parametrize("asset_path", ["daily", "interval"])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("fill_proof", ["open", "open_gap", "previous"])
@pytest.mark.parametrize("target", ["none", "tp2"])
def test_proven_fill_keeps_stop_lower_bound_and_target_ambiguity(asset_path, direction, fill_proof, target):
    row = _row(direction)
    opening = {"open": 100, "open_gap": 100.5, "previous": 98}[fill_proof]
    if fill_proof == "previous":
        row.update(entry_filled_at="2026-08-24T14:01:00+00:00", entry_fill_price=100)
    bar = _bar(direction, opening=opening, high=122 if target == "tp2" else 102)
    if asset_path == "daily":
        updates, failed = st._evaluate_stock_signal(row, lambda *_: [bar], NOW)
    else:
        observation = {
            "current": bar["close"], "interval_open": bar["open"],
            "interval_high": bar["high"], "interval_low": bar["low"],
            "interval_complete": True, "source": "test_5m",
            "started_at": "2026-08-26T11:55:00+00:00", "observed_at": NOW.isoformat(),
        }
        updates, failed = st._evaluate_crypto_signal(row, lambda *_, **__: observation, NOW)
    assert not failed
    merged = dict(row, **updates)
    assert merged["status"] == st.STATUS_STOP
    expected_fill = (100.5 if direction == "LONG" else 99.5) if fill_proof == "open_gap" else 100
    assert merged["entry_fill_price"] == expected_fill
    assert merged["entry_filled_at"]
    assert merged["r_realized"] == -1.0
    expected_upper = round(abs(row["tp2"] - expected_fill) / abs(expected_fill - row["stop"]), 4)
    assert merged["r_realized_upper"] == (expected_upper if target == "tp2" else -1.0)
    assert merged["stop_hit_at"]
    assert not st._entry_stop_path_is_unresolved(merged)
    if target == "tp2":
        expected_detail = "ambiguous_same_day_stop_and_tp2" if asset_path == "daily" else "ambiguous_same_interval_stop_and_tp2"
        assert merged["outcome_detail"] == expected_detail
        assert not merged["tp1_hit_at"]
    assert st._control_population_resolution(merged, NOW, require_mature=False) == "resolved"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_daily_existing_fill_keeps_real_gap_execution(direction):
    row = _row(direction, entry_filled_at="2026-08-24T14:01:00+00:00", entry_fill_price=100)
    bar = _bar(direction, opening=90, high=122, low=89, close=99)
    updates, failed = st._evaluate_stock_signal(row, lambda *_: [bar], NOW)
    assert not failed
    assert updates["status"] == st.STATUS_STOP
    assert updates["outcome_detail"] == "stop_gap_slippage"
    assert updates["r_realized"] == updates["r_realized_upper"] == -2.0
    assert updates["exit_fill_price"] == (90 if direction == "LONG" else 110)
    assert updates["stop_gap_slippage_r"] == 1.0
    assert "entry_fill_price" not in updates


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_daily_fill_stop_ambiguity_cannot_use_acceptance_day_prices(direction):
    row = _row(direction, delivery_accepted_at="2026-08-25T14:00:00+00:00")
    calls = []
    bars = [
        _bar(direction),  # both entry and stop on the acceptance day: unusable
        _bar(direction, day="2026-08-26", opening=98, high=99, low=97, close=98),
    ]
    later = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)

    def fetcher(ticker, since):
        calls.append((ticker, since))
        return bars

    updates, failed = st._evaluate_stock_signal(row, fetcher, later)
    assert not failed
    assert calls == [(row["ticker"], "2026-08-25")]
    assert "status" not in updates
    assert "entry_filled_at" not in updates
    assert "r_realized" not in updates


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("detail", LEGACY_DETAILS)
def test_legacy_unordered_entry_stop_is_read_only_unresolved_everywhere(direction, detail):
    row = _row(direction, status=st.STATUS_STOP, entry_filled_at="2026-08-25",
               entry_fill_price=100, stop_hit_at="2026-08-25", closed_at=NOW.isoformat(),
               r_realized=-1, r_realized_upper=4, outcome_detail=detail,
               fill_evidence_mode="post_alert_interval", mae_evidence_mode="intrabar_fill_order_unresolved")
    before = deepcopy(row)
    assert st._entry_stop_path_is_unresolved(row)
    for resolver in (
        st._realized_upper_resolution, st._managed_upper_resolution,
        st._breakeven_after_mfe_resolution, st._managed_5050_be_resolution,
        st._shadow_counterfactual_5050_be_resolution,
    ):
        assert resolver(row) == (None, True), resolver.__name__
    for resolver in (
        st._realized_upper, st._managed_upper_r, st._managed_r_50_50,
        st.simulate_breakeven_after_mfe, st.simulate_managed_5050_breakeven,
        st.breakeven_adjusted_r,
    ):
        assert resolver(row) is None, resolver.__name__
    assert st._managed_be_exit_resolution(row, 0) == (None, True)
    assert st._control_population_resolution(row, NOW, require_mature=False) == "unresolved"
    counts = st._control_population_counts([row], NOW, require_mature=False)
    assert counts == {"eligible": 1, "resolved": 0, "unresolved": 1, "no_fill": 0, "ambiguity_unresolved": 1}
    assert row == before


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("proof", ["prior_day", "prior_day_exact_path", "verified_snapshot"])
def test_detail_alone_cannot_erase_independently_proven_fill(direction, proof):
    row = _row(direction, status=st.STATUS_STOP, entry_filled_at="2026-08-25",
               entry_fill_price=100, stop_hit_at="2026-08-25", closed_at=NOW.isoformat(),
               r_realized=-1, r_realized_upper=4, max_favorable_r=0,
               outcome_detail="ambiguous_same_day_entry_stop_and_tp2")
    if proof in {"prior_day", "prior_day_exact_path"}:
        row["entry_filled_at"] = "2026-08-24T14:01:00+00:00"
        if proof == "prior_day_exact_path":
            row["mae_evidence_mode"] = "exact_post_fill_path"
    else:
        row.update(
            created_at="2026-08-25T14:00:00+00:00",
            entry_filled_at="2026-08-25T14:00:00+00:00",
            price_observed_at="2026-08-25T14:00:00+00:00",
            fill_evidence_mode="verified_snapshot", price_at_alert=100,
            fill_evidence_verified=1, price_source="live_quote_test",
            price_mode="ask" if direction == "LONG" else "bid",
            price_session="US_REGULAR",
        )
    before = deepcopy(row)
    assert not st._entry_stop_path_is_unresolved(row)
    assert st._managed_5050_be_resolution(row) == (-1, False)
    assert st._realized_upper_resolution(row) == (4, False)
    assert st._control_population_resolution(row, NOW, require_mature=False) == "resolved"
    assert row == before


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("tag", ["snapshot", "exact_path"])
def test_bare_evidence_labels_cannot_resolve_legacy_entry_stop(direction, tag):
    row = _row(direction, status=st.STATUS_STOP, entry_filled_at="2026-08-25",
               entry_fill_price=100, stop_hit_at="2026-08-25", closed_at=NOW.isoformat(),
               r_realized=-1, outcome_detail="ambiguous_same_day_entry_stop_and_tp2")
    row["fill_evidence_mode" if tag == "snapshot" else "mae_evidence_mode"] = (
        "verified_snapshot" if tag == "snapshot" else "exact_post_fill_path"
    )
    before = deepcopy(row)
    assert st._entry_stop_path_is_unresolved(row)
    assert st._managed_5050_be_resolution(row) == (None, True)
    assert st._control_population_resolution(row, NOW, require_mature=False) == "unresolved"
    assert row == before


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("bad_quote", [
    {"fill_evidence_verified": 0},
    {"fill_evidence_verified": "1"},
    {"price_source": None},
    {"price_source": "daily_close"},
    {"price_source": "unknown_provider"},
    {"price_observed_at": None},
    {"price_observed_at": "2026-08-25"},
    {"entry_filled_at": "2026-08-25"},
    {"price_observed_at": "2026-08-25T14:01:00+00:00"},
    {"price_at_alert": 100.01},
    {"price_at_alert": float("nan")},
    {"entry_fill_price": float("inf")},
    {"price_mode": "mid"},
    {"price_session": "closed"},
    {"direction": "INVALID"},
    {"created_at": "2026-08-25"},
    {"created_at": "2026-08-25T14:06:00+00:00"},
    {"closed_at": "2026-08-25T13:59:59+00:00"},
    {"stop_hit_at": "2026-08-25T13:59:59+00:00"},
    {"delivery_accepted_at": "2026-08-25T14:00:01+00:00"},
])
def test_snapshot_exemption_requires_original_executable_quote_and_causality(direction, bad_quote):
    row = _row(
        direction, created_at="2026-08-25T14:00:00+00:00", status=st.STATUS_STOP,
        entry_filled_at="2026-08-25T14:00:00+00:00", entry_fill_price=100,
        stop_hit_at="2026-08-25", closed_at=NOW.isoformat(), r_realized=-1,
        outcome_detail="ambiguous_same_day_entry_stop_and_tp2",
        fill_evidence_mode="verified_snapshot", fill_evidence_verified=True,
        price_at_alert=100, price_observed_at="2026-08-25T14:00:00+00:00",
        price_source="live_quote_test", price_mode="ask" if direction == "LONG" else "bid",
        price_session="US_REGULAR",
    )
    row.update(bad_quote)
    assert st._entry_stop_path_is_unresolved(row)
    assert st._managed_5050_be_resolution(row) == (None, True)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_verified_quote_still_must_cross_the_planned_entry_to_prove_fill(direction):
    row = _row(
        direction, created_at="2026-08-25T14:00:00+00:00", status=st.STATUS_STOP,
        entry=101 if direction == "LONG" else 99,
        entry_filled_at="2026-08-25T14:00:00+00:00", entry_fill_price=100,
        stop_hit_at="2026-08-25", closed_at=NOW.isoformat(), r_realized=-1,
        outcome_detail="ambiguous_same_day_entry_stop_and_tp2",
        fill_evidence_mode="verified_snapshot", fill_evidence_verified=1,
        price_at_alert=100, price_observed_at="2026-08-25T14:00:00+00:00",
        price_source="live_quote_test", price_mode="ask" if direction == "LONG" else "bid",
        price_session="US_REGULAR",
    )
    assert st._entry_stop_path_is_unresolved(row)
    assert st._managed_5050_be_resolution(row) == (None, True)
