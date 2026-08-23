import json
from datetime import datetime, timedelta, timezone

import pytest

from modules.level_zones import (
    BreakReclaimEvidence,
    LevelEvidence,
    StructureSnapshot,
    build_level_zones,
    build_structure_snapshot,
    classify_for_trade,
    completed_session_evidence,
    confirmed_pivot_evidence,
    evaluate_break_reclaim,
    legacy_level_adapter,
    normalize_completed_bars,
    select_trade_structure,
)


UTC = timezone.utc
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _bar(index, *, high, low, close, open_=None, closed=True):
    close_time = BASE + timedelta(days=index)
    return {
        "open_time": close_time - timedelta(days=1),
        "close_time": close_time,
        "open": close if open_ is None else open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1_000 + index,
        "is_closed": closed,
    }


def _evidence(
    price,
    *,
    family="horizontal_swing",
    name="level",
    timeframe="1D",
    cutoff=BASE,
    strength=1.0,
    projection_only=False,
    independence_key=None,
    break_state=None,
):
    provenance = {"touch_count": 1}
    if independence_key:
        provenance["independence_key"] = independence_key
    if break_state:
        provenance["break_state"] = break_state
    return LevelEvidence(
        source_family=family,
        source_name=name,
        timeframe=timeframe,
        lower=price,
        upper=price,
        observed_at=cutoff,
        confirmed_at=cutoff,
        data_cutoff_at=cutoff,
        strength=strength,
        provenance=provenance,
        projection_only=projection_only,
    )


def _snapshot(zones, *, current=100.0, as_of=BASE):
    return StructureSnapshot(
        symbol="TEST",
        asset_class="stock",
        horizon="swing",
        as_of=as_of,
        current_price=current,
        zones=tuple(zones),
        atr_by_timeframe={},
        completed_bar_counts={},
    )


def test_open_timestamp_is_not_completed_until_timeframe_has_elapsed():
    bar = {
        "timestamp": BASE,
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
    }

    assert normalize_completed_bars(
        [bar], timeframe="1D", as_of=BASE + timedelta(hours=12)
    ) == ()
    completed = normalize_completed_bars(
        [bar], timeframe="1D", as_of=BASE + timedelta(days=1)
    )
    assert len(completed) == 1
    assert completed[0].closed_at == BASE + timedelta(days=1)


def test_level_evidence_rejects_confirmation_before_observation():
    with pytest.raises(ValueError, match="confirmed before it was observed"):
        LevelEvidence(
            source_family="horizontal_swing",
            source_name="impossible level",
            timeframe="1D",
            lower=100.0,
            upper=100.0,
            observed_at=BASE + timedelta(days=1),
            confirmed_at=BASE,
            data_cutoff_at=BASE + timedelta(days=1),
        )


def test_pivot_exists_only_after_right_confirmation_bar_closes():
    bars = [
        _bar(1, high=11, low=9, close=10),
        _bar(2, high=12, low=10, close=11),
        _bar(3, high=15, low=11, close=13),
        _bar(4, high=12, low=10, close=11),
    ]

    before_confirmation = confirmed_pivot_evidence(
        bars, timeframe="1D", as_of=BASE + timedelta(days=3), pivot_left=1, pivot_right=1
    )
    assert not any(item.source_name == "confirmed_swing_high" for item in before_confirmation)

    after_confirmation = confirmed_pivot_evidence(
        bars, timeframe="1D", as_of=BASE + timedelta(days=4), pivot_left=1, pivot_right=1
    )
    swing_high = next(item for item in after_confirmation if item.source_name == "confirmed_swing_high")
    assert swing_high.observed_at == BASE + timedelta(days=3)
    assert swing_high.confirmed_at == BASE + timedelta(days=4)


def test_conflicting_duplicate_close_time_is_dropped_fail_closed():
    first = _bar(1, high=101.4, low=101.1, close=101.2)
    conflicting = _bar(1, high=101.6, low=101.05, close=101.3)

    completed = normalize_completed_bars(
        [first, conflicting],
        timeframe="1D",
        as_of=BASE + timedelta(days=1),
    )

    assert completed == ()


def test_snapshot_is_prefix_invariant_when_future_bars_are_appended():
    visible = [
        _bar(1, high=11, low=9, close=10),
        _bar(2, high=12, low=8, close=10),
        _bar(3, high=11, low=9, close=10),
        _bar(4, high=15, low=10, close=13),
        _bar(5, high=12, low=9, close=10),
    ]
    future = [
        _bar(6, high=50, low=2, close=40),
        _bar(7, high=60, low=1, close=3),
    ]
    cutoff = BASE + timedelta(days=5)

    prefix = build_structure_snapshot(
        {"1D": visible},
        symbol="ABC",
        asset_class="stock",
        horizon="swing",
        as_of=cutoff,
        current_price=13.0,
        atr_by_timeframe={"1D": 1.0},
        pivot_left=1,
        pivot_right=1,
    )
    with_future = build_structure_snapshot(
        {"1D": visible + future},
        symbol="ABC",
        asset_class="stock",
        horizon="swing",
        as_of=cutoff,
        current_price=13.0,
        atr_by_timeframe={"1D": 1.0},
        pivot_left=1,
        pivot_right=1,
    )

    assert prefix.to_dict() == with_future.to_dict()
    json.dumps(prefix.to_dict(), sort_keys=True)


def test_session_levels_ignore_running_session_even_if_timestamp_is_present():
    bars = [
        _bar(1, high=11, low=8, close=10),
        _bar(2, high=12, low=9, close=11),
        _bar(3, high=99, low=1, close=50, closed=False),
    ]

    evidence = completed_session_evidence(
        bars, timeframe="1D", as_of=BASE + timedelta(days=4)
    )

    assert {item.source_name: item.midpoint for item in evidence} == {
        "PDH": 12.0,
        "PDL": 9.0,
        "PDC": 11.0,
    }
    assert len({item.independence_key for item in evidence}) == 1


def test_adaptive_zones_merge_confluence_without_double_counting_fib_ratios():
    evidence = [
        _evidence(100.00, family="horizontal_swing", name="swing"),
        _evidence(100.08, family="vrvp", name="POC", timeframe="4H"),
        _evidence(
            100.04,
            family="fibonacci",
            name="FIB 38%",
            projection_only=True,
            independence_key="fib:leg-1",
        ),
        _evidence(
            100.09,
            family="fibonacci",
            name="FIB 61%",
            projection_only=True,
            independence_key="fib:leg-1",
        ),
    ]

    zones = build_level_zones(
        evidence,
        reference_price=105.0,
        atr_by_timeframe={"1D": 1.0, "4H": 1.0},
    )
    reversed_zones = build_level_zones(
        reversed(evidence),
        reference_price=105.0,
        atr_by_timeframe={"1D": 1.0, "4H": 1.0},
    )

    assert len(zones) == 1
    assert zones == reversed_zones
    zone = zones[0]
    assert zone.independent_sources == 3
    assert zone.independent_structural_sources == 2
    assert zone.projection_only is False
    assert zone.lower < 100.0 < zone.upper


def test_support_and_resistance_are_never_clustered_across_reference_price():
    zones = build_level_zones(
        [_evidence(99.99, name="below"), _evidence(100.01, family="vrvp", name="above")],
        reference_price=100.0,
        tick_size=0.01,
    )

    assert len(zones) == 2
    assert zones[0].side_at_reference == "support"
    assert zones[0].upper < 100.0
    assert zones[1].side_at_reference == "resistance"
    assert zones[1].lower > 100.0


def test_input_zone_crossing_reference_remains_overlap():
    crossing = LevelEvidence(
        source_family="vrvp",
        source_name="value area",
        timeframe="1D",
        lower=99.0,
        upper=100.2,
        observed_at=BASE,
        confirmed_at=BASE,
        data_cutoff_at=BASE,
    )

    zone = build_level_zones([crossing], reference_price=100.0)[0]

    assert zone.side_at_reference == "overlap"
    assert zone.lower == pytest.approx(99.0)
    assert zone.upper == pytest.approx(100.2)


def test_zone_id_is_stable_when_reference_crosses_and_clips_level():
    evidence = [_evidence(101.0, name="stable resistance")]

    below = build_level_zones(evidence, reference_price=100.0, tick_size=1.0)[0]
    above = build_level_zones(evidence, reference_price=102.0, tick_size=1.0)[0]

    assert below.side_at_reference == "resistance"
    assert above.side_at_reference == "support"
    assert (below.lower, below.upper) != (above.lower, above.upper)
    assert below.zone_id == above.zone_id


def test_projection_only_fib_zone_cannot_become_blocking_barrier():
    zones = build_level_zones(
        [
            _evidence(101.0, family="fibonacci", name="FIB 61%", projection_only=True),
            _evidence(103.0, family="horizontal_swing", name="confirmed high"),
        ],
        reference_price=100.0,
    )
    directional = classify_for_trade(_snapshot(zones), entry=100.0, direction="LONG")

    assert len(directional.resistances) == 2
    assert len(directional.opposing_barriers) == 1
    assert directional.opposing_barriers[0].reference == pytest.approx(103.0)


def test_fibonacci_ratios_from_one_leg_do_not_multiply_touch_strength():
    structural = _evidence(101.0, name="confirmed high", strength=0.7)
    fib_ratios = [
        _evidence(
            101.0 + index * 0.001,
            family="fibonacci",
            name=f"FIB ratio {index}",
            strength=0.5,
            projection_only=True,
            independence_key="fib:leg-1",
        )
        for index in range(8)
    ]

    one_ratio = build_level_zones(
        [structural, fib_ratios[0]],
        reference_price=100.0,
        atr_by_timeframe={"1D": 1.0},
    )[0]
    many_ratios = build_level_zones(
        [structural, *fib_ratios],
        reference_price=100.0,
        atr_by_timeframe={"1D": 1.0},
    )[0]

    assert one_ratio.independent_sources == many_ratios.independent_sources == 2
    assert one_ratio.touch_count == many_ratios.touch_count == 2
    assert one_ratio.strength == many_ratios.strength


def test_projection_only_intact_state_cannot_reset_reclaimed_structure():
    structural = _evidence(
        101.0,
        name="reclaimed resistance",
        break_state="reclaimed",
    )
    fib = _evidence(
        101.01,
        family="fibonacci",
        name="FIB 61%",
        projection_only=True,
        independence_key="fib:leg-1",
        break_state="intact",
    )

    zone = build_level_zones(
        [structural, fib],
        reference_price=100.0,
        atr_by_timeframe={"1D": 1.0},
    )[0]

    assert zone.break_state == "reclaimed"


def test_first_barrier_before_minimum_r_is_wait_not_skipped_target():
    near_zone = build_level_zones(
        [_evidence(102.0, name="first resistance")], reference_price=100.0
    )
    near = classify_for_trade(_snapshot(near_zone), entry=100.0, direction="LONG")
    decision = select_trade_structure(near, stop=98.0, minimum_rr=1.35)

    assert decision.status == "WAIT_BREAK_RECLAIM"
    assert decision.nearest_barrier.zone_id == near_zone[0].zone_id
    assert decision.barrier_r == pytest.approx(1.0)
    assert decision.target1 == pytest.approx(near_zone[0].lower)
    assert decision.barrier_gate == "BREAK_RECLAIM_REQUIRED"

    far_zone = build_level_zones(
        [_evidence(103.0, name="tradable resistance")], reference_price=100.0
    )
    far = classify_for_trade(_snapshot(far_zone), entry=100.0, direction="LONG")
    accepted = select_trade_structure(far, stop=98.0, minimum_rr=1.35)
    assert accepted.status == "ACCEPT"
    assert accepted.barrier_r == pytest.approx(1.5)


def test_trade_structure_fails_closed_without_confirmed_levels():
    snapshot = build_structure_snapshot(
        {},
        symbol="ABC",
        asset_class="stock",
        horizon="swing",
        as_of=BASE,
        current_price=100.0,
    )

    decision = select_trade_structure(
        classify_for_trade(snapshot, entry=100.0, direction="LONG"),
        stop=98.0,
    )

    assert snapshot.quality_flags == ("no_completed_bars", "no_confirmed_levels")
    assert decision.status == "REJECT"
    assert decision.reason == "structure_unavailable"
    assert decision.target1 is None
    assert decision.barrier_gate is None


def test_overlapping_barrier_forces_directional_break_target_long_and_short():
    overlap_evidence = LevelEvidence(
        source_family="horizontal_swing",
        source_name="active supply-demand zone",
        timeframe="1D",
        lower=99.0,
        upper=101.0,
        observed_at=BASE,
        confirmed_at=BASE,
        data_cutoff_at=BASE,
    )
    zone = build_level_zones([overlap_evidence], reference_price=100.0)[0]
    snapshot = _snapshot([zone])

    long_decision = select_trade_structure(
        classify_for_trade(snapshot, entry=100.0, direction="LONG"),
        stop=98.0,
        minimum_rr=0.0,
    )
    short_decision = select_trade_structure(
        classify_for_trade(snapshot, entry=100.0, direction="SHORT"),
        stop=102.0,
        minimum_rr=0.0,
    )

    assert long_decision.status == short_decision.status == "WAIT_BREAK_RECLAIM"
    assert long_decision.reason == short_decision.reason == "entry_overlaps_opposing_barrier"
    assert long_decision.barrier_distance == short_decision.barrier_distance == 0.0
    assert long_decision.barrier_r == short_decision.barrier_r == 0.0
    assert long_decision.target1 == pytest.approx(zone.upper)
    assert short_decision.target1 == pytest.approx(zone.lower)
    assert long_decision.barrier_gate == "BREAK_RECLAIM_REQUIRED"
    assert short_decision.barrier_gate == "BREAK_SUPPORT_REQUIRED"


def test_reclaim_requires_completed_break_close_and_subsequent_hold_retest():
    zone = build_level_zones(
        [_evidence(101.0, name="resistance")],
        reference_price=100.0,
        tick_size=0.05,
    )[0]
    bars = [
        _bar(1, high=101.5, low=100.7, close=101.0),  # wick only
        _bar(2, high=101.4, low=101.0, close=101.2),  # completed break close
        _bar(3, high=101.4, low=101.05, close=101.2, closed=False),
    ]

    pending = evaluate_break_reclaim(
        zone,
        bars,
        as_of=BASE + timedelta(days=3),
        direction="LONG",
        timeframe="1D",
        hold_bars=1,
        require_retest=True,
    )
    assert pending.state == "RECLAIM_PENDING"
    assert pending.hold_bars_observed == 0

    bars.append(_bar(4, high=101.4, low=101.05, close=101.15))
    reclaimed = evaluate_break_reclaim(
        zone,
        bars,
        as_of=BASE + timedelta(days=4),
        direction="LONG",
        timeframe="1D",
        hold_bars=1,
        require_retest=True,
    )
    assert reclaimed.state == "RECLAIMED"
    assert reclaimed.hold_bars_observed == 1
    assert reclaimed.retest_observed is True
    payload = reclaimed.to_dict()
    assert payload["zone_confirmed_at"] == "2026-01-01T00:00:00Z"
    assert payload["timeframe"] == "1D"
    assert payload["as_of"] == "2026-01-05T00:00:00Z"
    assert payload["break_closed_at"] == "2026-01-03T00:00:00Z"
    assert payload["last_completed_at"] == "2026-01-05T00:00:00Z"
    assert payload["last_completed_close"] == pytest.approx(101.15)
    assert (
        reclaimed.zone_confirmed_at
        < reclaimed.break_closed_at
        <= reclaimed.last_completed_at
        <= reclaimed.as_of
    )
    assert json.loads(json.dumps(payload, sort_keys=True)) == payload


def test_snapshot_does_not_flip_crossed_resistance_without_completed_reclaim():
    resistance = LevelEvidence(
        source_family="horizontal_swing",
        source_name="confirmed_swing_high",
        timeframe="1D",
        lower=101.0,
        upper=101.0,
        observed_at=BASE,
        confirmed_at=BASE,
        data_cutoff_at=BASE,
        provenance={"role_hint": "resistance"},
    )
    break_only = [_bar(1, high=101.5, low=101.15, close=101.3)]

    pending = build_structure_snapshot(
        {"1D": break_only},
        symbol="ABC",
        asset_class="stock",
        horizon="swing",
        as_of=BASE + timedelta(days=1),
        current_price=102.0,
        tick_size=0.05,
        external_evidence=[resistance],
        include_session_levels=False,
        pivot_left=1,
        pivot_right=1,
    )

    pending_zone = next(zone for zone in pending.zones if zone.zone_id)
    assert pending_zone.origin_roles == ("resistance",)
    assert pending_zone.break_state == "intact"
    assert pending_zone.break_reclaim_evidence.state == "RECLAIM_PENDING"
    assert "crossed_resistance_reclaim_pending" in pending_zone.quality_flags
    assert "crossed_level_reclaim_pending" in pending.quality_flags

    reclaimed = build_structure_snapshot(
        {"1D": break_only + [_bar(2, high=101.4, low=101.08, close=101.25)]},
        symbol="ABC",
        asset_class="stock",
        horizon="swing",
        as_of=BASE + timedelta(days=2),
        current_price=102.0,
        tick_size=0.05,
        external_evidence=[resistance],
        include_session_levels=False,
        pivot_left=1,
        pivot_right=1,
    )

    reclaimed_zone = next(zone for zone in reclaimed.zones if zone.zone_id == pending_zone.zone_id)
    assert reclaimed_zone.break_state == "reclaimed"
    assert reclaimed_zone.break_reclaim_evidence.state == "RECLAIMED"
    assert "former_resistance_reclaimed" in reclaimed_zone.quality_flags
    assert "crossed_level_reclaim_pending" not in reclaimed.quality_flags


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "zone_confirmed_at": BASE + timedelta(days=4),
                "as_of": BASE + timedelta(days=3),
            },
            "zone confirmation cannot be after",
        ),
        (
            {"break_closed_at": BASE},
            "break close must occur after zone confirmation",
        ),
        (
            {
                "break_closed_at": BASE + timedelta(days=3),
                "last_completed_at": BASE + timedelta(days=2),
            },
            "active break close cannot be after",
        ),
    ],
)
def test_break_reclaim_evidence_rejects_invalid_time_order(overrides, message):
    values = {
        "state": "RECLAIM_PENDING",
        "reason": "completed_hold_bars_missing",
        "direction": "LONG",
        "zone_id": "lz_test",
        "boundary": 101.0,
        "zone_confirmed_at": BASE + timedelta(days=1),
        "timeframe": "1D",
        "as_of": BASE + timedelta(days=4),
        "break_closed_at": BASE + timedelta(days=2),
        "last_completed_at": BASE + timedelta(days=3),
        "last_completed_close": 101.2,
        "hold_bars_required": 1,
        "hold_bars_observed": 0,
        "retest_required": False,
        "retest_observed": False,
        "completed_bars_used": 2,
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        BreakReclaimEvidence(**values)


def test_break_reclaim_ignores_bars_before_zone_confirmation():
    confirmed_at = BASE + timedelta(days=3)
    zone = build_level_zones(
        [_evidence(101.0, name="late-confirmed resistance", cutoff=confirmed_at)],
        reference_price=100.0,
    )[0]
    # _evidence observes and confirms at its cutoff. Both otherwise-valid bars
    # precede the point at which this resistance became knowable.
    before_confirmation = [
        _bar(1, high=101.5, low=101.1, close=101.2),
        _bar(2, high=101.4, low=101.05, close=101.15),
    ]

    ignored = evaluate_break_reclaim(
        zone,
        before_confirmation,
        as_of=confirmed_at,
        direction="LONG",
        timeframe="1D",
        hold_bars=1,
    )

    assert ignored.state == "INTACT"
    assert ignored.break_closed_at is None
    assert ignored.completed_bars_used == 0

    post_confirmation = before_confirmation + [
        _bar(4, high=101.5, low=101.1, close=101.2),
        _bar(5, high=101.4, low=101.05, close=101.15),
    ]
    reclaimed = evaluate_break_reclaim(
        zone,
        post_confirmation,
        as_of=BASE + timedelta(days=5),
        direction="LONG",
        timeframe="1D",
        hold_bars=1,
    )

    assert reclaimed.state == "RECLAIMED"
    assert reclaimed.break_closed_at == BASE + timedelta(days=4)
    assert reclaimed.completed_bars_used == 2


def test_duplicate_close_time_cannot_count_as_break_and_subsequent_hold():
    zone = build_level_zones(
        [_evidence(101.0, name="resistance")],
        reference_price=100.0,
    )[0]
    first = _bar(1, high=101.4, low=101.1, close=101.2)
    duplicate = _bar(1, high=101.6, low=101.05, close=101.3)

    result = evaluate_break_reclaim(
        zone,
        [first, duplicate],
        as_of=BASE + timedelta(days=1),
        direction="LONG",
        timeframe="1D",
        hold_bars=1,
    )

    assert result.state == "INTACT"
    assert result.hold_bars_observed == 0
    assert result.completed_bars_used == 0


def test_break_reclaim_is_prefix_invariant_at_fixed_cutoff():
    zone = build_level_zones(
        [_evidence(101.0, name="resistance")],
        reference_price=100.0,
    )[0]
    visible = [
        _bar(1, high=101.5, low=101.1, close=101.2),
        _bar(2, high=101.4, low=101.05, close=101.15),
    ]
    future = _bar(3, high=110.0, low=90.0, close=95.0)
    cutoff = BASE + timedelta(days=2)

    prefix = evaluate_break_reclaim(
        zone,
        visible,
        as_of=cutoff,
        direction="LONG",
        timeframe="1D",
        hold_bars=1,
    )
    with_future = evaluate_break_reclaim(
        zone,
        visible + [future],
        as_of=cutoff,
        direction="LONG",
        timeframe="1D",
        hold_bars=1,
    )

    assert prefix.to_dict() == with_future.to_dict()


def test_short_barrier_and_break_hold_logic_are_directionally_symmetric():
    zone = build_level_zones(
        [_evidence(99.0, name="support")], reference_price=100.0
    )[0]
    directional = classify_for_trade(_snapshot([zone]), entry=100.0, direction="SHORT")
    decision = select_trade_structure(directional, stop=102.0, minimum_rr=1.0)
    assert decision.status == "WAIT_BREAK_RECLAIM"
    assert decision.barrier_gate == "BREAK_SUPPORT_REQUIRED"

    bars = [
        _bar(1, high=99.2, low=98.6, close=98.8),
        _bar(2, high=99.1, low=98.7, close=98.9),
    ]
    reclaimed = evaluate_break_reclaim(
        zone,
        bars,
        as_of=BASE + timedelta(days=2),
        direction="SHORT",
        timeframe="1D",
        hold_bars=1,
        require_retest=True,
    )
    assert reclaimed.state == "RECLAIMED"
    assert reclaimed.retest_observed is True


@pytest.mark.parametrize(
    ("direction", "level", "break_bar", "hold_bar", "rollback_bar", "rollback_close"),
    [
        (
            "LONG",
            101.0,
            (101.5, 101.1, 101.2),
            (101.4, 101.05, 101.15),
            (101.1, 100.7, 100.9),
            100.9,
        ),
        (
            "SHORT",
            99.0,
            (99.0, 98.6, 98.8),
            (99.1, 98.7, 98.9),
            (99.3, 98.9, 99.2),
            99.2,
        ),
    ],
)
def test_latest_completed_close_invalidates_prior_reclaim(
    direction, level, break_bar, hold_bar, rollback_bar, rollback_close
):
    zone = build_level_zones(
        [_evidence(level, name="barrier")],
        reference_price=100.0,
    )[0]
    initial = [
        _bar(1, high=break_bar[0], low=break_bar[1], close=break_bar[2]),
        _bar(2, high=hold_bar[0], low=hold_bar[1], close=hold_bar[2]),
    ]

    reclaimed = evaluate_break_reclaim(
        zone,
        initial,
        as_of=BASE + timedelta(days=2),
        direction=direction,
        timeframe="1D",
        hold_bars=1,
    )
    assert reclaimed.state == "RECLAIMED"
    assert reclaimed.last_completed_at == BASE + timedelta(days=2)

    rolled_back = evaluate_break_reclaim(
        zone,
        initial + [
            _bar(
                3,
                high=rollback_bar[0],
                low=rollback_bar[1],
                close=rollback_bar[2],
            )
        ],
        as_of=BASE + timedelta(days=3),
        direction=direction,
        timeframe="1D",
        hold_bars=1,
    )

    assert rolled_back.state == "INTACT"
    assert rolled_back.break_closed_at is None
    assert rolled_back.hold_bars_observed == 0
    assert rolled_back.last_completed_at == BASE + timedelta(days=3)
    assert rolled_back.last_completed_close == pytest.approx(rollback_close)


def test_external_evidence_after_snapshot_cutoff_is_rejected():
    future = _evidence(101.0, cutoff=BASE + timedelta(days=1))

    with pytest.raises(ValueError, match="crosses snapshot as_of"):
        build_structure_snapshot(
            {},
            symbol="ABC",
            asset_class="stock",
            horizon="swing",
            as_of=BASE,
            current_price=100.0,
            external_evidence=[future],
        )


def test_legacy_adapter_is_deterministic_and_json_serializable():
    zones = build_level_zones(
        [_evidence(98.0, name="support"), _evidence(103.0, name="resistance")],
        reference_price=100.0,
    )
    snapshot = _snapshot(zones)

    first = legacy_level_adapter(snapshot, direction="LONG")
    second = legacy_level_adapter(snapshot, direction="LONG")

    assert first == second
    assert first["supports"][0]["price"] == pytest.approx(98.0)
    assert first["resistances"][0]["price"] == pytest.approx(103.0)
    json.dumps(first, sort_keys=True)
