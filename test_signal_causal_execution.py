from datetime import datetime, timezone

import pytest

from modules import signal_tracker as st


def _row(direction="LONG", **extra):
    sign = 1 if direction == "LONG" else -1
    result = dict(
        ticker="CAUSAL", scanner="bi_long" if sign == 1 else "bi_short", mail_class="trade",
        entry=100.0, entry_fill_price=100.0, stop=100-sign*5,
        tp1=100+sign*10, tp2=100+sign*20, direction=direction,
        created_at="2026-08-24T14:00:00+00:00", entry_filled_at="2026-08-24T14:01:00+00:00",
        be_trigger_at="2026-08-24T16:00:00+00:00", be_activated_at="2026-08-24T16:00:00+00:00",
        be_mail_sent_at="2026-08-24T16:01:00+00:00", be_delivery_evidence_key="fr1_"+"A"*43,
        be_exit_at="2026-08-25T14:00:00+00:00", be_exit_fill_price=100.0,
        be_exit_evidence_mode="completed_interval_open_or_entry_level",
        tp1_hit_at="2026-08-26T14:00:00+00:00", tp2_hit_at="2026-08-27T14:00:00+00:00",
        status=st.STATUS_TP2, r_realized=4.0, max_favorable_r=4.0, evaluation_horizon_bars=20,
    )
    result.update(extra)
    return result


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("shadow", [False, True])
@pytest.mark.parametrize("order,expected", [("later", 0.0), ("earlier", 1.0), ("equal", None)])
def test_partial_target_cannot_postdate_whole_position_be_exit(direction, shadow, order, expected):
    row = _row(direction)
    if order == "earlier":
        row["tp1_hit_at"] = "2026-08-24T17:00:00+00:00"
    elif order == "equal":
        row["tp1_hit_at"] = row["be_exit_at"]
    resolver = st._managed_5050_be_resolution
    if shadow:
        row["be_exit_evidence_mode"] = "shadow_counterfactual_completed_interval_open_or_entry_level"
        resolver = st._shadow_counterfactual_5050_be_resolution
    assert resolver(row) == (expected, expected is None)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_be_gap_before_partial_target_applies_to_entire_position(direction):
    row = _row(direction, be_exit_fill_price=98 if direction == "LONG" else 102)
    assert st._managed_5050_be_resolution(row) == (-0.4, False)


@pytest.mark.parametrize("order,expected", [("before", 1.0), ("not_reached", 0.0), ("unknown", None)])
def test_same_bar_partial_requires_explicit_ordered_evidence(order, expected):
    row = _row(be_exit_tp1_order=order)
    row["tp1_hit_at"] = row["be_exit_at"]
    assert st._managed_5050_be_resolution(row) == (expected, expected is None)


def test_daily_evaluator_preserves_separate_level_and_be_lifecycles():
    row = _row(status=st.STATUS_OPEN, r_realized=None, be_exit_at=None, be_exit_fill_price=None,
               tp1_hit_at=None, tp2_hit_at=None, max_favorable_r=1)
    bars = [dict(date="2026-08-25", open=101, high=105, low=99, close=101),
            dict(date="2026-08-26", open=102, high=112, low=101, close=110),
            dict(date="2026-08-27", open=111, high=121, low=110, close=120)]
    updates, failed = st._evaluate_stock_signal(row, lambda *a: bars, datetime(2026,8,28,12,tzinfo=timezone.utc))
    assert not failed
    assert updates["be_exit_at"] < updates["tp1_hit_at"]
    assert updates["status"] == st.STATUS_TP2
    assert st._managed_5050_be_resolution(dict(row, **updates)) == (0.0, False)
    assert updates["evaluation_model_version"] == st.EVALUATION_MODEL_VERSION


@pytest.mark.parametrize("asset_path", ["daily", "interval"])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("terminal", ["target", "stop"])
def test_terminal_extrema_exclude_post_exit_prices(asset_path, direction, terminal):
    row = _row(direction, status=st.STATUS_OPEN, r_realized=None, max_favorable_r=0,
               be_trigger_at=None, be_activated_at=None, be_mail_sent_at=None,
               be_exit_at=None, be_exit_fill_price=None, tp1_hit_at=None, tp2_hit_at=None)
    high, low = ((130,99) if terminal == "target" else (102,80))
    if direction == "SHORT":
        high, low = 200-low, 200-high
    now = datetime(2026,8,25,21,tzinfo=timezone.utc)
    if asset_path == "daily":
        bar = dict(date="2026-08-25", open=100, high=high, low=low, close=100)
        updates, failed = st._evaluate_stock_signal(row, lambda *a:[bar], now)
    else:
        observation = dict(current=100, interval_open=100, interval_high=high, interval_low=low,
                           interval_complete=True, source="test_5m", started_at="2026-08-25T20:55:00+00:00",
                           observed_at=now.isoformat())
        updates, failed = st._evaluate_crypto_signal(row, lambda *a,**k:observation, now)
    assert not failed
    assert updates["max_favorable_r"] == (4 if terminal == "target" else 0)
    assert updates["max_adverse_r"] == (0 if terminal == "target" else -1)
    assert updates["path_extrema_evidence_mode"] == "terminal_intrabar_bounds"


def test_shared_cohort_uses_acceptance_and_retains_only_trade_population():
    as_of = datetime(2026,9,4,tzinfo=timezone.utc)
    base = _row(created_at="2026-08-01T00:00:00+00:00", evaluation_horizon_bars=1)
    rows = [dict(base, id=1, delivery_accepted_at="2026-09-03T00:00:00+00:00"),
            dict(base, id=2, mail_class="shadow"), dict(base, id=3, status=st.STATUS_PENDING_DELIVERY),
            dict(base, id=4)]
    mature, _ = st.select_performance_cohort(rows, days=60, as_of=as_of)
    assert [r["id"] for r in mature] == [4]
    recent, _ = st.select_performance_cohort(rows, days=7, as_of=as_of, mature_only=False)
    assert [r["id"] for r in recent] == [1]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_daily_invalidation_before_entry_cannot_revive_on_a_later_bar(direction):
    row = _row(direction, status=st.STATUS_OPEN, entry_filled_at=None, entry_fill_price=None,
               r_realized=None, be_exit_at=None, be_exit_fill_price=None, tp1_hit_at=None, tp2_hit_at=None)
    bar = dict(date="2026-08-25", open=94, high=98, low=90, close=96)
    if direction == "SHORT":
        bar.update(open=106, high=110, low=102, close=104)
    updates, failed = st._evaluate_stock_signal(row, lambda *a:[bar], datetime(2026,8,26,12,tzinfo=timezone.utc))
    assert not failed
    assert updates["status"] == st.STATUS_NO_FILL
    assert updates["outcome_detail"] == "entry_invalidated_before_fill"
    assert "entry_fill_price" not in updates


@pytest.mark.parametrize("asset_path", ["daily", "interval"])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_gap_through_original_stop_also_records_causal_be_execution(asset_path, direction):
    row = _row(direction, status=st.STATUS_OPEN, r_realized=None, be_exit_at=None,
               be_exit_fill_price=None, tp1_hit_at=None, tp2_hit_at=None, max_favorable_r=1)
    opening = 90 if direction == "LONG" else 110
    now = datetime(2026,8,25,21,tzinfo=timezone.utc)
    if asset_path == "daily":
        bar = dict(date="2026-08-25", open=opening, high=opening+1, low=opening-1, close=opening)
        updates, failed = st._evaluate_stock_signal(row, lambda *a:[bar], now)
    else:
        observation = dict(current=opening, interval_open=opening, interval_high=opening+1,
            interval_low=opening-1, interval_complete=True, source="test_5m",
            started_at="2026-08-25T20:55:00+00:00", observed_at=now.isoformat())
        updates, failed = st._evaluate_crypto_signal(row, lambda *a,**k:observation, now)
    assert not failed
    assert updates["be_exit_fill_price"] == opening
    assert st._managed_5050_be_resolution(dict(row, **updates)) == (-2.0, False)


def test_daily_timestamp_does_not_claim_tp1_precedes_same_day_intraday_be():
    row = _row(tp1_hit_at="2026-08-25")
    assert st._managed_5050_be_resolution(row) == (None, True)


@pytest.mark.parametrize("scanner", sorted(st.CRYPTO_SCANNERS | set(st._STOCK_HORIZON_BY_SCANNER)))
def test_shared_execution_geometry_and_be_legs_apply_to_every_registered_asset_scanner(scanner):
    row = _row(scanner=scanner)
    assert st._managed_5050_be_resolution(row) == (0.0, False)
    assert st.validate_fill_quality(scanner, 100, 100, 95, 110, 120, "LONG")["valid"]
