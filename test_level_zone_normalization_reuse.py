"""Canonical snapshot reuse must reduce work without weakening public validation."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from modules import level_zones as levels


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
CUTOFF = BASE + timedelta(days=5)


def _bar(day, high, low, close, **extra):
    return {
        "open_time": BASE + timedelta(days=day - 1),
        "close_time": BASE + timedelta(days=day),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
        **extra,
    }


def _noisy_bars():
    # Only days 1, 2, 3 and 5 survive. Day 2 is a strict high and low pivot.
    return [
        _bar(5, 105, 101, 104),
        _bar(2, 104, 99, 102),
        _bar(4, 110, 95, 103),
        _bar(1, 102, 100, 101),
        _bar(3, 103, 100, 102),
        _bar(4, 111, 94, 104),  # Conflicting close instant: neither wins.
        _bar(2, 104, 99, 102),  # Identical duplicate: counts only once.
        _bar(6, 150, 50, 140),  # Future bar cannot create evidence.
        _bar(5.5, 180, 30, 170, is_closed=False),
        _bar(0, 1, 90, 100),  # Invalid OHLC cannot create evidence.
    ]


def _evidence(price, role):
    return levels.LevelEvidence(
        source_family="horizontal_swing",
        source_name=f"known_{role}_{price}",
        timeframe="1D",
        lower=price,
        upper=price,
        observed_at=BASE,
        confirmed_at=BASE,
        data_cutoff_at=BASE,
        provenance={"role_hint": role},
    )


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("include_sessions", [False, True])
def test_snapshot_normalization_work_is_bounded_by_input_timeframes(
    monkeypatch, direction, include_sessions
):
    # Reintroducing public normalizing consumers inside the snapshot must fail
    # this deterministic work budget even if their outputs remain identical.
    original = levels.normalize_completed_bars
    normalized_inputs = []

    def record_real_normalization(bars, **kwargs):
        normalized_inputs.append((kwargs["timeframe"], len(bars)))
        return original(bars, **kwargs)

    monkeypatch.setattr(levels, "normalize_completed_bars", record_real_normalization)
    role = "resistance" if direction == "LONG" else "support"
    prices = (91, 93, 95) if direction == "LONG" else (107, 109, 111)
    snapshot = levels.build_structure_snapshot(
        {"D": _noisy_bars(), "1W": _noisy_bars(), "4H": _noisy_bars()},
        symbol="TEST", asset_class="stock", horizon="swing", as_of=CUTOFF,
        current_price=115 if direction == "LONG" else 85,
        external_evidence=[_evidence(price, role) for price in prices],
        pivot_left=1, pivot_right=1, include_session_levels=include_sessions,
    )

    assert snapshot.completed_bar_counts == {"1D": 4, "1W": 4, "4H": 4}
    transitions = [zone.break_reclaim_evidence for zone in snapshot.zones
                   if zone.break_reclaim_evidence is not None]
    assert len(transitions) >= 3
    assert {item.direction for item in transitions} == {direction}
    assert sorted(normalized_inputs) == [("1D", 10), ("1W", 10), ("4H", 10)]


@pytest.mark.parametrize("as_completed_bars", [False, True])
def test_public_evidence_still_canonicalizes_unordered_conflicts_and_cutoff(as_completed_bars):
    # Skipping normalization merely because the caller supplied a tuple or
    # CompletedBar instances would leak conflicts/future bars and wrong indices.
    bars = _noisy_bars()
    if as_completed_bars:
        # CompletedBar's existing contract trusts OHLC and completion status;
        # use only its valid rows but keep conflict, duplicate and future rows.
        bars = tuple(levels.CompletedBar(
            opened_at=bar["open_time"], closed_at=bar["close_time"],
            open=bar["open"], high=bar["high"], low=bar["low"],
            close=bar["close"], volume=bar["volume"], source_index=90 - index,
        ) for index, bar in enumerate(bars[:8]))

    canonical = levels.normalize_completed_bars(bars, timeframe="D", as_of=CUTOFF)
    assert [(bar.closed_at, bar.close, bar.source_index) for bar in canonical] == [
        (BASE + timedelta(days=1), 101, 0),
        (BASE + timedelta(days=2), 102, 1),
        (BASE + timedelta(days=3), 102, 2),
        (BASE + timedelta(days=5), 104, 3),
    ]
    pivots = levels.confirmed_pivot_evidence(
        bars, timeframe="D", as_of=CUTOFF, pivot_left=1, pivot_right=1,
    )
    assert [(item.source_name, item.lower, item.observed_at, item.confirmed_at,
             item.provenance["pivot_index"], item.provenance["confirmation_bar_index"])
            for item in pivots] == [
        ("confirmed_swing_high", 104, BASE + timedelta(days=2), BASE + timedelta(days=3), 1, 2),
        ("confirmed_swing_low", 99, BASE + timedelta(days=2), BASE + timedelta(days=3), 1, 2),
    ]
    sessions = levels.completed_session_evidence(bars, timeframe="D", as_of=CUTOFF)
    assert [(item.source_name, item.lower, item.confirmed_at) for item in sessions] == [
        ("PDH", 105, CUTOFF), ("PDL", 101, CUTOFF), ("PDC", 104, CUTOFF),
    ]
    snapshot = levels.build_structure_snapshot(
        {"D": bars}, symbol="TEST", asset_class="stock", horizon="swing",
        as_of=CUTOFF, current_price=102, pivot_left=1, pivot_right=1,
    )
    assert snapshot.completed_bar_counts == {"1D": 4}
    assert sorted((item.source_name, item.lower, item.confirmed_at)
                  for zone in snapshot.zones for item in zone.evidence) == [
        ("PDC", 104, CUTOFF), ("PDH", 105, CUTOFF), ("PDL", 101, CUTOFF),
        ("confirmed_swing_high", 104, BASE + timedelta(days=3)),
        ("confirmed_swing_low", 99, BASE + timedelta(days=3)),
    ]


@pytest.mark.parametrize("direction,level,bars,expected_close", [
    ("LONG", 101, [_bar(1, 102, 101, 101.5), _bar(2, 102, 100.9, 101.2)], 101.2),
    ("SHORT", 99, [_bar(1, 99, 98, 98.5), _bar(2, 99.1, 98, 98.8)], 98.8),
])
@pytest.mark.parametrize("as_completed_bars", [False, True])
def test_snapshot_reclaim_reuses_canonical_bars_with_unchanged_causal_result(
    direction, level, bars, expected_close, as_completed_bars
):
    role = "resistance" if direction == "LONG" else "support"
    evidence = _evidence(level, role)
    supplied = [bars[1], bars[0], bars[0], _bar(3, 200, 1, 100)]
    if as_completed_bars:
        supplied = tuple(levels.CompletedBar(
            opened_at=bar["open_time"], closed_at=bar["close_time"],
            open=bar["open"], high=bar["high"], low=bar["low"],
            close=bar["close"], volume=bar["volume"], source_index=40 - index,
        ) for index, bar in enumerate(supplied))
    snapshot = levels.build_structure_snapshot(
        {"4H": supplied},
        symbol="TEST", asset_class="stock", horizon="swing",
        as_of=BASE + timedelta(days=2), current_price=103 if direction == "LONG" else 97,
        external_evidence=[evidence], include_session_levels=False,
    )
    assert len(snapshot.zones) == 1
    zone = snapshot.zones[0]
    assert zone.break_state == "reclaimed"
    assert zone.quality_flags == (f"former_{role}_reclaimed", "single_independent_source")
    expected = {
        "model": "break_reclaim_close_hold_v1",
        "state": "RECLAIMED", "reason": "completed_break_close_and_hold_confirmed",
        "direction": direction, "zone_id": zone.zone_id,
        "boundary": pytest.approx(level, abs=1e-12, rel=0),
        "zone_confirmed_at": "2026-01-01T00:00:00Z", "timeframe": "4H",
        "as_of": "2026-01-03T00:00:00Z", "break_closed_at": "2026-01-02T00:00:00Z",
        "last_completed_at": "2026-01-03T00:00:00Z", "last_completed_close": expected_close,
        "hold_bars_required": 1, "hold_bars_observed": 1,
        "retest_required": True, "retest_observed": True, "completed_bars_used": 2,
    }
    assert zone.break_reclaim_evidence.to_dict() == expected
    public_result = levels.evaluate_break_reclaim(
        zone, supplied, as_of=BASE + timedelta(days=2), direction=direction,
        timeframe="4H", require_retest=True,
    )
    assert public_result.to_dict() == expected


def test_snapshot_honors_explicit_early_session_close():
    opened = datetime(2025, 11, 28, 14, 30, tzinfo=timezone.utc)
    closed = datetime(2025, 11, 28, 18, tzinfo=timezone.utc)
    bars = [{"open_time": opened, "close_time": closed,
             "open": 100, "high": 102, "low": 99, "close": 101}]
    before = levels.build_structure_snapshot(
        {"D": bars}, symbol="TEST", asset_class="stock", horizon="swing",
        as_of=closed - timedelta(seconds=1), current_price=101,
    )
    assert before.zones == ()
    assert before.completed_bar_counts == {"1D": 0}
    after = levels.build_structure_snapshot(
        {"D": bars}, symbol="TEST", asset_class="stock", horizon="swing",
        as_of=closed, current_price=101,
    )
    assert after.completed_bar_counts == {"1D": 1}
    assert sorted((item.source_name, item.lower, item.confirmed_at)
                  for zone in after.zones for item in zone.evidence) == [
        ("PDC", 101, closed), ("PDH", 102, closed), ("PDL", 99, closed),
    ]


@pytest.mark.parametrize("api", ["pivot", "session", "break"])
def test_public_completed_tuple_input_still_rejects_invalid_timestamp_mode(api):
    bars = levels.normalize_completed_bars(_noisy_bars(), timeframe="1D", as_of=CUTOFF)
    with pytest.raises(ValueError, match="timestamp_mode"):
        if api == "pivot":
            levels.confirmed_pivot_evidence(bars, timeframe="1D", as_of=CUTOFF, timestamp_mode="bad")
        elif api == "session":
            levels.completed_session_evidence(bars, timeframe="1D", as_of=CUTOFF, timestamp_mode="bad")
        else:
            zone = levels.build_level_zones([_evidence(101, "resistance")], reference_price=100)[0]
            levels.evaluate_break_reclaim(
                zone, bars, timeframe="1D", as_of=CUTOFF, direction="LONG", timestamp_mode="bad",
            )


@pytest.mark.parametrize("bars_by_timeframe,counts", [({}, {}), ({"D": [], "4H": []}, {"1D": 0, "4H": 0})])
def test_empty_snapshot_remains_fail_closed(bars_by_timeframe, counts):
    snapshot = levels.build_structure_snapshot(
        bars_by_timeframe, symbol="TEST", asset_class="stock", horizon="swing",
        as_of=CUTOFF, current_price=100,
    )
    assert snapshot.completed_bar_counts == counts
    assert snapshot.zones == ()
    assert snapshot.quality_flags == ("no_completed_bars", "no_confirmed_levels")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("candidate_kind", ["opposing", "invalidation"])
@pytest.mark.parametrize("composition,constituents,is_trade_structure", [
    ("session_reference", [("session", "reference", False)], False),
    ("reference_named_pdh", [("session", "reference", False)], True),
    ("reference_with_structural_role", [("session", "reference", False)], True),
    ("reference_with_projection", [
        ("session", "reference", False), ("fibonacci", None, True),
    ], False),
    ("reference_with_high", [
        ("session", "reference", False), ("session", "resistance", False),
    ], True),
    ("reference_with_low", [
        ("session", "reference", False), ("session", "support", False),
    ], True),
    ("reference_with_legacy_structure", [
        ("session", "reference", False), ("horizontal_swing", None, False),
    ], True),
    ("legacy_without_role", [("horizontal_swing", None, False)], True),
    ("session_without_role", [("session", None, False)], True),
    ("other_family_reference", [("horizontal_swing", "reference", False)], True),
    ("projection_only", [("fibonacci", None, True)], False),
    ("legacy_empty_evidence", [("horizontal_swing", None, False)], True),
])
def test_only_pure_session_references_are_excluded_from_trade_candidates(
    direction, candidate_kind, composition, constituents, is_trade_structure
):
    # Treating PDC as structure invents barriers/stops. Conversely, checking
    # only origin_roles or source names would erase genuine legacy structure.
    evidence = [levels.LevelEvidence(
        source_family=family, source_name=f"fixture_{index}", timeframe="1D",
        lower=price, upper=price, observed_at=BASE, confirmed_at=BASE,
        data_cutoff_at=BASE, projection_only=projection,
        provenance={"role_hint": role} if role is not None else {},
    ) for price in (95, 105) for index, (family, role, projection) in enumerate(constituents)]
    if composition == "reference_named_pdh":
        evidence = [replace(item, source_name="PDH") for item in evidence]
    elif composition == "reference_with_structural_role":
        evidence = [replace(item, provenance={**item.provenance, "role": "resistance"})
                    for item in evidence]
    snapshot = levels.build_structure_snapshot(
        {}, symbol="TEST", asset_class="stock", horizon="swing", as_of=BASE,
        current_price=100, external_evidence=evidence,
    )
    if composition == "legacy_empty_evidence":
        snapshot = replace(snapshot, zones=tuple(replace(zone, evidence=()) for zone in snapshot.zones))
    directional = levels.classify_for_trade(snapshot, entry=100, direction=direction)
    if len(constituents) > 1:
        # The independent reference annotation must not become part of the
        # actual high/low, legacy or projection zone being tested.
        candidates = [zone for zone in snapshot.zones if "fixture_1" in zone.source_names]
        references = [zone for zone in snapshot.zones if zone.source_names == ("fixture_0",)]
        assert len(references) == 2
        assert all(zone not in directional.opposing_barriers
                   and zone not in directional.invalidation_candidates for zone in references)
    else:
        candidates = list(snapshot.zones)
    lower, upper = candidates
    # Informational zones remain visible, regardless of their eligibility.
    assert {zone.zone_id for zone in directional.supports} == {
        zone.zone_id for zone in snapshot.zones if zone.upper < 100}
    assert {zone.zone_id for zone in directional.resistances} == {
        zone.zone_id for zone in snapshot.zones if zone.lower > 100}
    assert directional.snapshot is snapshot
    expected_barrier = upper if direction == "LONG" else lower
    expected_invalidation = lower if direction == "LONG" else upper
    if candidate_kind == "opposing":
        assert directional.opposing_barriers == ((expected_barrier,) if is_trade_structure else ())
    else:
        assert directional.invalidation_candidates == ((expected_invalidation,) if is_trade_structure else ())
