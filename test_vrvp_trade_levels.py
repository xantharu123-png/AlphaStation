from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from modules.trade_levels import minimum_stop_distance, trade_geometry
from modules.indicators import calculate_atr_14
from modules.vrvp_levels import (
    apply_vrvp_to_trade_setup,
    build_vrvp_structure,
    calculate_wilder_atr,
)
from modules.volume_analysis import merge_lvn_bins


def _bars_with_nodes(low_node: float = 95.0, high_node: float = 112.0):
    bars = []
    for i in range(24):
        base = low_node + (i % 3) * 0.18
        bars.append({
            "open": base,
            "high": base + 0.35,
            "low": base - 0.35,
            "close": base + 0.05,
            "volume": 40_000,
        })
    for i in range(28):
        base = high_node + (i % 4) * 0.22
        bars.append({
            "open": base,
            "high": base + 0.45,
            "low": base - 0.45,
            "close": base - 0.03,
            "volume": 120_000,
        })
    for i in range(12):
        base = 103 + (i % 4) * 0.35
        bars.append({
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base,
            "volume": 18_000,
        })
    return bars


def _with_daily_close_times(bars, start):
    return [
        {
            **bar,
            "close_time": (start + timedelta(days=index + 1)).isoformat(),
        }
        for index, bar in enumerate(bars)
    ]


def _build_causal_daily_structure(bars, current_price, direction):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    timestamped = _with_daily_close_times(bars, start)
    return build_vrvp_structure(
        timestamped,
        current_price,
        direction,
        timeframe="1D",
        min_bars=20,
        as_of=start + timedelta(days=len(timestamped) + 2),
        timestamp_mode="close",
    )


def _causal_vrvp_level(
    price,
    source,
    kind,
    weight=1.5,
    *,
    timeframe="4H",
    profile_id="vrvp-profile-test",
):
    width = max(float(price) * 0.002, 0.01)
    zone_low = float(price) - width / 2
    zone_high = float(price) + width / 2
    zone_token = str(price).replace(".", "_")
    return {
        "price": float(price),
        "source": source,
        "kind": kind,
        "weight": weight,
        "source_family": "vrvp",
        "timeframe": timeframe,
        "profile_id": profile_id,
        "independence_key": profile_id,
        "zone_id": f"vrvp-zone-{profile_id}-{kind}-{zone_token}",
        "zone_low": zone_low,
        "zone_high": zone_high,
        "confirmed_at": "2025-01-31T20:00:00Z",
        "data_cutoff_at": "2025-01-31T20:00:00Z",
        "causal_structure_validated": True,
    }


def _causalize_levels(levels, *, timeframe="4H", profile_id="vrvp-profile-test"):
    def inferred_kind(level):
        if level.get("kind"):
            return level["kind"]
        source = str(level.get("source") or "").upper()
        return next((kind for kind in ("POC", "VAH", "VAL", "HVN", "LVN_EDGE") if kind in source), "")

    return [
        _causal_vrvp_level(
            level["price"],
            level["source"],
            inferred_kind(level),
            level.get("weight", 1.5),
            timeframe=timeframe,
            profile_id=profile_id,
        )
        for level in levels
    ]


def _vrvp_structure_signature(structure):
    return {
        key: structure[key]
        for key in (
            "poc",
            "vah",
            "val",
            "range_high",
            "range_low",
            "supports",
            "resistances",
            "levels",
            "volume_voids",
        )
    }


def test_timestamped_vrvp_is_prefix_invariant_to_future_and_unclosed_extremes():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    base_bars = _with_daily_close_times(_bars_with_nodes(), start)
    cutoff = start + timedelta(days=len(base_bars) + 1)
    baseline = build_vrvp_structure(
        base_bars,
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=40,
        as_of=cutoff,
        timestamp_mode="close",
    )

    future_extremes = [
        {
            "open": 900.0 + index,
            "high": 1_200.0 + index,
            "low": 700.0 + index,
            "close": 1_000.0 + index,
            "volume": 9_000_000_000,
            "close_time": (cutoff + timedelta(days=index + 1)).isoformat(),
        }
        for index in range(8)
    ]
    explicitly_unclosed = {
        "open": 0.02,
        "high": 5_000.0,
        "low": 0.01,
        "close": 4_000.0,
        "volume": 99_000_000_000,
        "close_time": (cutoff - timedelta(minutes=1)).isoformat(),
        "is_closed": False,
    }
    augmented = build_vrvp_structure(
        base_bars + future_extremes + [explicitly_unclosed],
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=40,
        as_of=cutoff,
        timestamp_mode="close",
    )

    assert baseline is not None
    assert augmented is not None
    assert _vrvp_structure_signature(augmented) == _vrvp_structure_signature(baseline)
    assert augmented["causal_completion_verified"] is True
    assert augmented["completion_filter_mode"] == "timestamped_completed_only"
    assert augmented["provenance"]["lookback_applied_after_completion_filter"] is True
    assert augmented["provenance"]["completed_before_lookback_count"] == len(base_bars)
    assert augmented["provenance"]["excluded_not_causally_completed_count"] == 9


def test_open_timestamp_bar_that_has_not_closed_cannot_change_vrvp_structure():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    base_bars = _with_daily_close_times(_bars_with_nodes(), start)
    cutoff = start + timedelta(days=len(base_bars) + 1)
    baseline = build_vrvp_structure(
        base_bars,
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=35,
        as_of=cutoff,
    )
    open_extreme = {
        "open": 3_000.0,
        "high": 5_000.0,
        "low": 2_500.0,
        "close": 4_500.0,
        "volume": 999_000_000_000,
        "open_time": (cutoff - timedelta(hours=2)).isoformat(),
    }
    augmented = build_vrvp_structure(
        base_bars + [open_extreme],
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=35,
        as_of=cutoff,
    )

    assert baseline is not None
    assert augmented is not None
    assert _vrvp_structure_signature(augmented) == _vrvp_structure_signature(baseline)
    assert augmented["provenance"]["excluded_not_causally_completed_count"] == 1


@pytest.mark.parametrize(
    ("date_session_context", "cutoff", "expected_semantics"),
    [
        (
            "conservative_calendar_day",
            datetime(2025, 7, 15, 23, 0, tzinfo=timezone.utc),
            "calendar_interval_closes_next_utc_boundary",
        ),
        (
            "us_equity_regular",
            datetime(2025, 7, 15, 19, 0, tzinfo=timezone.utc),
            "us_equity_regular_session_16_et",
        ),
    ],
)
def test_date_only_future_and_running_daily_bars_cannot_change_vrvp(
    date_session_context, cutoff, expected_semantics
):
    raw = _bars_with_nodes()
    first_day = cutoff.date() - timedelta(days=len(raw))
    completed = [
        {
            **bar,
            "date": (first_day + timedelta(days=index)).isoformat(),
        }
        for index, bar in enumerate(raw)
    ]
    baseline = build_vrvp_structure(
        completed,
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=40,
        as_of=cutoff,
        date_session_context=date_session_context,
    )
    running_daily = {
        "open": 2_500.0,
        "high": 5_000.0,
        "low": 2_000.0,
        "close": 4_000.0,
        "volume": 900_000_000_000,
        "date": cutoff.date().isoformat(),
    }
    future_daily = {
        "open": 0.03,
        "high": 8_000.0,
        "low": 0.01,
        "close": 7_000.0,
        "volume": 990_000_000_000,
        "date": (cutoff.date() + timedelta(days=1)).isoformat(),
    }
    augmented = build_vrvp_structure(
        completed + [running_daily, future_daily],
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=40,
        as_of=cutoff,
        date_session_context=date_session_context,
    )

    assert baseline is not None
    assert augmented is not None
    assert _vrvp_structure_signature(augmented) == _vrvp_structure_signature(baseline)
    provenance = augmented["provenance"]
    assert augmented["causal_completion_verified"] is True
    assert augmented["date_session_context"] == date_session_context
    assert augmented["date_completion_semantics"] == expected_semantics
    assert provenance["completed_before_lookback_count"] == len(completed)
    assert provenance["excluded_not_causally_completed_count"] == 2
    assert provenance["date_temporal_adapted_count"] == len(completed) + 2
    assert provenance["date_session_context"] == date_session_context
    assert provenance["date_completion_semantics"] == expected_semantics


def test_identical_timestamped_duplicate_is_deduped_before_vrvp_lookback():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = _with_daily_close_times(_bars_with_nodes(), start)
    cutoff = start + timedelta(days=len(bars) + 1)
    baseline = build_vrvp_structure(
        bars,
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=40,
        as_of=cutoff,
        timestamp_mode="close",
    )
    duplicated = build_vrvp_structure(
        bars + [dict(bars[-1])],
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=40,
        as_of=cutoff,
        timestamp_mode="close",
    )

    assert baseline is not None
    assert duplicated is not None
    assert _vrvp_structure_signature(duplicated) == _vrvp_structure_signature(baseline)
    provenance = duplicated["provenance"]
    assert provenance["raw_completed_before_dedup_count"] == len(bars) + 1
    assert provenance["completed_before_lookback_count"] == len(bars)
    assert provenance["identical_duplicate_count"] == 1
    assert provenance["conflicting_duplicate_count"] == 0
    assert provenance["duplicate_conflict_fail_closed"] is False


def test_conflicting_timestamped_duplicate_rejects_entire_vrvp_batch():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = _with_daily_close_times(_bars_with_nodes(), start)
    cutoff = start + timedelta(days=len(bars) + 1)
    conflict = dict(bars[-1])
    conflict.update({
        "open": 450.0,
        "high": 700.0,
        "low": 400.0,
        "close": 650.0,
        "volume": 9_000_000_000,
    })

    rejected = build_vrvp_structure(
        bars + [conflict],
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=40,
        as_of=cutoff,
        timestamp_mode="close",
    )

    assert rejected is None


def test_same_close_with_conflicting_open_interval_rejects_entire_vrvp_batch():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = _with_daily_close_times(_bars_with_nodes(), start)
    cutoff = start + timedelta(days=len(bars) + 1)
    conflict = {
        **bars[-1],
        "open_time": (start + timedelta(days=len(bars) - 1, hours=1)).isoformat(),
    }

    rejected = build_vrvp_structure(
        bars + [conflict],
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        as_of=cutoff,
        timestamp_mode="close",
    )

    assert rejected is None


def test_causal_vrvp_levels_expose_reclaimable_zone_identity():
    structure = _build_causal_daily_structure(_bars_with_nodes(), 100.0, "LONG")

    assert structure is not None
    assert structure["causal_structure_validated"] is True
    assert structure["profile_confirmed_at"]
    assert structure["profile_id"].startswith("vrvp-profile-")
    assert structure["levels"]
    for level in structure["levels"]:
        assert level["causal_structure_validated"] is True
        assert level["zone_id"].startswith("vrvp-zone-")
        assert level["confirmed_at"] == structure["profile_confirmed_at"]
        assert 0 < level["zone_low"] <= level["zone_high"]
        assert level["independence_key"] == structure["profile_id"]


def test_legacy_untimestamped_vrvp_remains_compatible_but_is_marked_unverified():
    structure = build_vrvp_structure(
        _bars_with_nodes(),
        100.0,
        "LONG",
        timeframe="1D",
        min_bars=20,
        lookback=40,
        as_of="2025-03-31T00:00:00Z",
    )

    assert structure is not None
    assert structure["bars"] == 40
    assert structure["causal_completion_verified"] is False
    assert structure["completion_filter_mode"] == "legacy_no_timestamps"
    assert structure["provenance"]["legacy_without_timestamps"] is True
    assert structure["provenance"]["lookback_applied_after_completion_filter"] is False
    assert structure["levels"] == []
    assert structure["unverified_profile_level_count"] > 0


def test_vrvp_lifts_long_targets_to_structural_resistance():
    vrvp = _build_causal_daily_structure(_bars_with_nodes(), 100, "LONG")
    setup = {
        "entry": 100,
        "stop": 97,
        "tp1": 104,
        "tp2": 106,
        "direction": "LONG",
        "level_model": "structure_first_v2",
    }

    enriched = apply_vrvp_to_trade_setup(setup, vrvp, direction="LONG", asset_type="stock_swing", atr=2.0)

    assert enriched["tp1"] > enriched["entry"]
    assert enriched["tp2"] > enriched["tp1"]
    assert enriched["rr_tp1"] >= 1.5
    assert enriched["rr_tp2"] >= 2.4
    assert enriched["rr"] >= 1.95
    assert enriched["vrvp_poc"] is not None
    assert "vrvp" in enriched["level_model"]
    assert trade_geometry(
        enriched["entry"], enriched["stop"], enriched["tp1"], enriched["tp2"], "LONG"
    )["valid"] is True


def test_adjacent_lvn_bins_form_one_void_with_only_outer_edges(monkeypatch):
    raw_lvns = [
        {"low": 101.0, "high": 102.0, "mid": 101.5, "volume": 10, "volume_pct": 20},
        {"low": 102.0, "high": 103.0, "mid": 102.5, "volume": 8, "volume_pct": 16},
        {"low": 103.0, "high": 104.0, "mid": 103.5, "volume": 9, "volume_pct": 18},
    ]
    zones = merge_lvn_bins(raw_lvns)
    assert len(zones) == 1
    assert zones[0]["low"] == 101.0
    assert zones[0]["high"] == 104.0
    assert zones[0]["bin_count"] == 3

    profile = {
        "poc": 98.0,
        "vah": 99.0,
        "val": 96.0,
        "range_high": 110.0,
        "range_low": 90.0,
        "avg_volume": 100.0,
        "hvns": [],
        "lvns": raw_lvns,
    }
    monkeypatch.setattr("modules.vrvp_levels.calculate_volume_profile", lambda *_args, **_kwargs: profile)
    bars = _with_daily_close_times([
        {"open": 99.0, "high": 100.0, "low": 98.0, "close": 99.0, "volume": 1000}
        for _ in range(20)
    ], datetime(2025, 1, 1, tzinfo=timezone.utc))
    structure = build_vrvp_structure(
        bars,
        100.0,
        "LONG",
        min_bars=20,
        as_of="2025-02-01T00:00:00Z",
        timestamp_mode="close",
    )
    lvn_levels = [level for level in structure["levels"] if level["kind"] == "LVN_EDGE"]
    assert lvn_levels == []
    assert structure["volume_voids"][0]["bin_count"] == 3


def test_vrvp_short_targets_remain_below_entry_and_separate():
    vrvp = _build_causal_daily_structure(
        _bars_with_nodes(low_node=88, high_node=105), 100, "SHORT"
    )
    setup = {
        "entry": 100,
        "stop": 103,
        "tp1": 96,
        "tp2": 94,
        "direction": "SHORT",
        "level_model": "structure_first_v2",
    }

    enriched = apply_vrvp_to_trade_setup(setup, vrvp, direction="SHORT", asset_type="stock_swing", atr=2.0)

    assert enriched["stop"] > enriched["entry"]
    assert enriched["tp1"] < enriched["entry"]
    assert enriched["tp2"] < enriched["tp1"]
    assert enriched["rr_tp1"] >= 1.5
    assert enriched["rr_tp2"] >= 2.4
    assert enriched["rr"] >= 1.95
    assert enriched["vrvp_poc"] is not None
    assert trade_geometry(
        enriched["entry"], enriched["stop"], enriched["tp1"], enriched["tp2"], "SHORT"
    )["valid"] is True


def test_vrvp_validation_prevents_duplicate_targets():
    setup = {
        "entry": 10,
        "stop": 9.5,
        "tp1": 10.2,
        "tp2": 10.2,
        "direction": "LONG",
    }

    enriched = apply_vrvp_to_trade_setup(setup, None, direction="LONG", asset_type="crypto")

    assert enriched["vrvp_applied"] is False
    assert enriched["tp1"] == 10.2
    assert enriched["tp2"] == 10.2


def test_vrvp_marks_near_overhead_resistance_as_long_gate():
    vrvp = {
        "timeframe": "4H",
        "resistances": _causalize_levels([
            {"price": 100.55, "source": "VRVP HVN high", "weight": 2.2},
            {"price": 106.0, "source": "VRVP VAH", "weight": 1.3},
        ], timeframe="4H", profile_id="near-long"),
        "supports": _causalize_levels(
            [{"price": 98.8, "source": "VRVP POC", "weight": 1.5}],
            timeframe="4H",
            profile_id="near-long",
        ),
    }
    setup = {
        "entry": 100.0,
        "stop": 99.35,
        "tp1": 103.0,
        "tp2": 106.0,
        "direction": "LONG",
        "trade_action": "TRADE_NOW",
        "entry_status": "TRIGGER_OK",
        "signal_quality": "tradeable",
        "structure_decision": {"status": "ACCEPT", "barrier_gate": None},
        "starter_plan": {"status": "ANTICIPATION"},
        "starter_entry": 99.8,
    }

    enriched = apply_vrvp_to_trade_setup(setup, vrvp, direction="LONG", asset_type="crypto")

    assert enriched["nearest_barrier"]["side"] == "resistance"
    assert enriched["overhead_resistance"]["price"] == 100.55
    assert enriched["barrier_gate"] == "BREAK_RECLAIM_REQUIRED"
    assert "near_overhead_resistance" in enriched["risk_flags"]
    assert enriched["tp1"] == 100.55
    assert enriched["tp1_source"] == "VRVP HVN high"
    assert enriched["tp1_is_projection"] is False
    assert enriched["rr_tp1"] < enriched["nearest_barrier"]["minimum_rr"]
    assert enriched["tp2"] > enriched["tp1"]
    assert enriched["tp2"] != 106.0
    assert enriched["tp2_is_projection"] is True
    assert enriched["tp2_projection_reason"] == "no_second_independent_structural_barrier"
    assert enriched["nearest_barrier"]["zone_id"].startswith("vrvp-zone-")
    assert enriched["nearest_barrier"]["confirmed_at"] == "2025-01-31T20:00:00Z"
    assert enriched["nearest_barrier"]["reclaim_boundary"] == enriched["nearest_barrier"]["zone_high"]
    assert enriched["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert enriched["structure_decision"]["status"] == "WAIT_BREAK_RECLAIM"
    assert enriched["structure_decision"]["barrier_gate"] == "BREAK_RECLAIM_REQUIRED"
    assert enriched["trade_action"] == "WAIT_FOR_BREAK_RECLAIM"
    assert enriched["entry_status"] == "WAIT_FOR_BREAK_RECLAIM"
    assert enriched["signal_quality"] == "wait_trigger"
    assert "starter_plan" not in enriched
    assert "starter_entry" not in enriched


def test_vrvp_marks_near_underlying_support_as_short_gate():
    vrvp = {
        "timeframe": "4H",
        "supports": _causalize_levels([
            {"price": 99.45, "source": "VRVP HVN low", "weight": 2.0},
            {"price": 94.0, "source": "VRVP VAL", "weight": 1.4},
        ], timeframe="4H", profile_id="near-short"),
        "resistances": _causalize_levels(
            [{"price": 101.2, "source": "VRVP POC", "weight": 1.2}],
            timeframe="4H",
            profile_id="near-short",
        ),
    }
    setup = {
        "entry": 100.0,
        "stop": 100.65,
        "tp1": 97.0,
        "tp2": 94.0,
        "direction": "SHORT",
    }

    enriched = apply_vrvp_to_trade_setup(setup, vrvp, direction="SHORT", asset_type="crypto")

    assert enriched["nearest_barrier"]["side"] == "support"
    assert enriched["underlying_support"]["price"] == 99.45
    assert enriched["barrier_gate"] == "BREAK_SUPPORT_REQUIRED"
    assert "near_underlying_support" in enriched["risk_flags"]
    assert enriched["tp1"] == 99.45
    assert enriched["tp1_source"] == "VRVP HVN low"
    assert enriched["tp1_is_projection"] is False
    assert enriched["rr_tp1"] < enriched["nearest_barrier"]["minimum_rr"]
    assert enriched["tp2"] < enriched["tp1"]
    assert enriched["tp2"] != 94.0
    assert enriched["tp2_is_projection"] is True
    assert enriched["nearest_barrier"]["reclaim_boundary"] == enriched["nearest_barrier"]["zone_low"]


@pytest.mark.parametrize(
    ("direction", "stop", "first", "second", "support_key", "resistance_key"),
    [
        ("LONG", 98.0, 103.2, 108.0, [], [
            {"price": 103.2, "source": "VRVP VAH", "kind": "VAH", "weight": 1.7},
            {"price": 108.0, "source": "VRVP HVN high", "kind": "HVN", "weight": 1.4},
        ]),
        ("SHORT", 102.0, 96.8, 92.0, [
            {"price": 96.8, "source": "VRVP VAL", "kind": "VAL", "weight": 1.7},
            {"price": 92.0, "source": "VRVP HVN low", "kind": "HVN", "weight": 1.4},
        ], []),
    ],
)
def test_first_tradable_barrier_is_tp1_and_same_profile_second_level_is_projection(
    direction, stop, first, second, support_key, resistance_key
):
    vrvp = {
        "timeframe": "1D",
        "source": "ohlcv_volume_profile",
        "approximation": True,
        "profile_method": "proportional_bar_volume_by_price_overlap",
        "supports": _causalize_levels(
            support_key, timeframe="1D", profile_id=f"same-profile-{direction.lower()}"
        ),
        "resistances": _causalize_levels(
            resistance_key, timeframe="1D", profile_id=f"same-profile-{direction.lower()}"
        ),
    }
    setup = {
        "entry": 100.0,
        "stop": stop,
        "tp1": 110.0 if direction == "LONG" else 90.0,
        "tp2": 120.0 if direction == "LONG" else 80.0,
        "direction": direction,
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction=direction, asset_type="stock_swing"
    )

    assert enriched["tp1"] == first
    assert enriched["tp2"] != second
    assert enriched["tp2"] > first if direction == "LONG" else enriched["tp2"] < first
    assert enriched["tp1_structure"] == "first_opposing_vrvp_barrier"
    assert enriched["tp2_structure"] == "projection_after_first_structural_barrier"
    assert enriched["tp1_is_projection"] is False
    assert enriched["tp2_is_projection"] is True
    assert enriched["tp2_projection_reason"] == "no_second_independent_structural_barrier"
    assert enriched["vrvp_first_barrier"]["price"] == first
    assert enriched["vrvp_first_barrier"]["profile_approximation"] is True
    assert enriched["barrier_gate"] is None


@pytest.mark.parametrize(
    ("direction", "stop", "barrier", "supports", "resistances"),
    [
        ("LONG", 99.0, 100.6, [], [
            {"price": 100.6, "source": "VRVP POC", "kind": "POC", "weight": 2.2},
        ]),
        ("SHORT", 101.0, 99.4, [
            {"price": 99.4, "source": "VRVP POC", "kind": "POC", "weight": 2.2},
        ], []),
    ],
)
def test_single_close_barrier_keeps_honest_tp1_and_labels_tp2_projection(
    direction, stop, barrier, supports, resistances
):
    vrvp = {
        "timeframe": "4H",
        "supports": _causalize_levels(
            supports, timeframe="4H", profile_id=f"single-{direction.lower()}"
        ),
        "resistances": _causalize_levels(
            resistances, timeframe="4H", profile_id=f"single-{direction.lower()}"
        ),
    }
    setup = {
        "entry": 100.0,
        "stop": stop,
        "tp1": 104.0 if direction == "LONG" else 96.0,
        "tp2": 108.0 if direction == "LONG" else 92.0,
        "direction": direction,
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction=direction, asset_type="stock_swing"
    )

    assert enriched["tp1"] == barrier
    assert enriched["tp1_is_projection"] is False
    assert enriched["tp2_is_projection"] is True
    assert enriched["tp2_projection_reason"] == "no_second_independent_structural_barrier"
    assert "projection fallback" in enriched["tp2_source"]
    assert enriched["barrier_gate"] in {
        "BREAK_RECLAIM_REQUIRED", "BREAK_SUPPORT_REQUIRED"
    }
    assert trade_geometry(
        enriched["entry"], enriched["stop"], enriched["tp1"], enriched["tp2"], direction
    )["valid"] is True


@pytest.mark.parametrize(
    ("direction", "stop", "price", "zone_low", "zone_high", "expected_r", "basis", "gate"),
    [
        ("LONG", 99.0, 101.6, 100.4, 101.8, 0.4, "zone_low", "BREAK_RECLAIM_REQUIRED"),
        ("SHORT", 101.0, 98.4, 98.2, 99.6, 0.4, "zone_high", "BREAK_SUPPORT_REQUIRED"),
        ("LONG", 99.0, 100.3, 99.8, 100.8, 0.0, "zone_low", "BREAK_RECLAIM_REQUIRED"),
        ("SHORT", 101.0, 99.7, 99.2, 100.2, 0.0, "zone_high", "BREAK_SUPPORT_REQUIRED"),
    ],
)
def test_first_barrier_distance_uses_near_zone_edge_and_overlap(
    direction, stop, price, zone_low, zone_high, expected_r, basis, gate
):
    level = _causal_vrvp_level(
        price,
        "VRVP POC",
        "POC",
        2.2,
        timeframe="4H",
        profile_id=f"zone-edge-{direction.lower()}-{price}",
    )
    level.update({
        "zone_low": zone_low,
        "zone_high": zone_high,
        "lower": zone_low,
        "upper": zone_high,
    })
    vrvp = {
        "timeframe": "4H",
        "supports": [level] if direction == "SHORT" else [],
        "resistances": [level] if direction == "LONG" else [],
    }
    setup = {
        "entry": 100.0,
        "stop": stop,
        "tp1": 104.0 if direction == "LONG" else 96.0,
        "tp2": 108.0 if direction == "LONG" else 92.0,
        "direction": direction,
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction=direction, asset_type="intraday"
    )
    barrier = enriched["nearest_barrier"]

    assert barrier["distance_r"] == expected_r
    assert barrier["distance_basis"] == basis
    assert barrier["entry_boundary"] == (zone_low if direction == "LONG" else zone_high)
    assert barrier["entry_inside_zone"] is (expected_r == 0.0)
    assert barrier["below_minimum_reward"] is True
    assert enriched["barrier_gate"] == gate
    assert enriched["structure_status"] == "WAIT_BREAK_RECLAIM"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_first_barrier_selection_uses_nearest_zone_edge_not_node_price(direction):
    if direction == "LONG":
        scalar_near = _causal_vrvp_level(101.0, "VRVP VAH", "VAH", profile_id="edge-order-long-a")
        zone_near = _causal_vrvp_level(102.0, "VRVP POC", "POC", profile_id="edge-order-long-b")
        scalar_near.update({"zone_low": 100.8, "zone_high": 101.2})
        zone_near.update({"zone_low": 100.2, "zone_high": 102.2})
        supports, resistances, stop, expected = [], [scalar_near, zone_near], 99.0, 102.0
    else:
        scalar_near = _causal_vrvp_level(99.0, "VRVP VAL", "VAL", profile_id="edge-order-short-a")
        zone_near = _causal_vrvp_level(98.0, "VRVP POC", "POC", profile_id="edge-order-short-b")
        scalar_near.update({"zone_low": 98.8, "zone_high": 99.2})
        zone_near.update({"zone_low": 97.8, "zone_high": 99.8})
        supports, resistances, stop, expected = [scalar_near, zone_near], [], 101.0, 98.0
    vrvp = {"timeframe": "4H", "supports": supports, "resistances": resistances}

    enriched = apply_vrvp_to_trade_setup(
        {
            "entry": 100.0,
            "stop": stop,
            "tp1": 104.0 if direction == "LONG" else 96.0,
            "tp2": 108.0 if direction == "LONG" else 92.0,
            "direction": direction,
        },
        vrvp,
        direction=direction,
        asset_type="intraday",
    )

    assert enriched["vrvp_first_barrier"]["price"] == expected
    assert enriched["nearest_barrier"]["price"] == expected
    assert enriched["nearest_barrier"]["distance_r"] == 0.2


def test_lvn_edge_is_not_promoted_to_structural_tp1():
    vrvp = {
        "timeframe": "4H",
        "supports": [],
        "resistances": [
            {"price": 100.4, "source": "VRVP LVN upper edge", "kind": "LVN_EDGE", "weight": 1.0},
            _causal_vrvp_level(
                104.0, "VRVP POC", "POC", 2.2,
                timeframe="4H", profile_id="lvn-filter",
            ),
        ],
    }
    setup = {
        "entry": 100.0,
        "stop": 99.0,
        "tp1": 103.0,
        "tp2": 107.0,
        "direction": "LONG",
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="stock_swing"
    )

    assert enriched["tp1"] == 104.0
    assert enriched["vrvp_first_barrier"]["kind"] == "POC"
    assert enriched["vrvp_first_barrier"]["price"] == 104.0


def test_lvn_only_profile_preserves_existing_valid_targets():
    vrvp = {
        "timeframe": "4H",
        "supports": [],
        "resistances": [
            {"price": 100.4, "source": "VRVP LVN upper edge", "kind": "LVN_EDGE", "weight": 1.0},
        ],
    }
    setup = {
        "entry": 100.0,
        "stop": 99.0,
        "tp1": 103.0,
        "tp2": 106.0,
        "direction": "LONG",
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="stock_swing"
    )

    assert enriched["tp1"] == 103.0
    assert enriched["tp2"] == 106.0
    assert enriched["vrvp_applied"] is False
    assert "vrvp_first_barrier" not in enriched
    assert "barrier_gate" not in enriched
    assert "tp2_projection_reason" not in enriched


def test_vrvp_cannot_skip_a_closer_confirmed_level_zone_barrier():
    setup = {
        "entry": 100.0,
        "stop": 99.0,
        "tp1": 101.0,
        "tp2": 106.0,
        "direction": "LONG",
        "tp1_is_projection": False,
        "tp2_is_projection": True,
        "nearest_barrier": {
            "price": 101.0,
            "side": "resistance",
            "source": "confirmed 4H swing zone",
            "timeframe": "4H",
            "zone_id": "lz-first",
            "zone_low": 100.9,
            "zone_high": 101.1,
            "confirmed_at": "2025-01-30T20:00:00Z",
            "causal_structure_validated": True,
            "structural": True,
            "action": "BREAK_RECLAIM_REQUIRED",
        },
        "barrier_gate": "BREAK_RECLAIM_REQUIRED",
    }
    vrvp = {
        "timeframe": "1D",
        "supports": [],
        "resistances": [
            _causal_vrvp_level(
                104.0, "VRVP VAH", "VAH", 2.0,
                timeframe="1D", profile_id="vrvp-independent-daily",
            ),
        ],
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="stock_swing"
    )

    assert enriched["tp1"] == 101.0
    assert enriched["tp1_source"] == "confirmed 4H swing zone"
    assert enriched["tp2"] == 104.0
    assert enriched["nearest_barrier"]["zone_id"] == "lz-first"
    assert enriched["nearest_barrier"]["canonical_source_family"] == "level_zone"
    assert enriched["barrier_gate"] == "BREAK_RECLAIM_REQUIRED"


def test_closer_vrvp_barrier_precedes_a_farther_existing_structure_target():
    setup = {
        "entry": 100.0,
        "stop": 99.0,
        "tp1": 105.0,
        "tp2": 108.0,
        "direction": "LONG",
        "tp1_is_projection": False,
        "tp2_is_projection": False,
        "nearest_barrier": {
            "price": 105.0,
            "side": "resistance",
            "source": "confirmed daily swing zone",
            "timeframe": "1D",
            "zone_id": "lz-farther",
            "zone_low": 104.8,
            "zone_high": 105.2,
            "confirmed_at": "2025-01-30T20:00:00Z",
            "causal_structure_validated": True,
            "structural": True,
            "action": None,
        },
        "barrier_gate": "BREAK_RECLAIM_REQUIRED",
        "structure_status": "WAIT_BREAK_RECLAIM",
        "structure_decision": {
            "status": "WAIT_BREAK_RECLAIM",
            "barrier_gate": "BREAK_RECLAIM_REQUIRED",
        },
        "trade_action": "WAIT_FOR_BREAK_RECLAIM",
        "entry_status": "WAIT_FOR_BREAK_RECLAIM",
        "signal_quality": "wait_trigger",
    }
    vrvp = {
        "timeframe": "4H",
        "supports": [],
        "resistances": [
            _causal_vrvp_level(
                104.0, "VRVP POC", "POC", 2.1,
                timeframe="4H", profile_id="vrvp-nearer-4h",
            ),
        ],
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="stock_swing"
    )

    assert enriched["tp1"] == 104.0
    assert enriched["tp2"] == 105.0
    assert enriched["nearest_barrier"]["source"] == "VRVP POC"
    assert enriched["nearest_barrier"]["canonical_source_family"] == "vrvp"
    assert enriched["barrier_gate"] is None
    assert enriched["structure_status"] == "ACCEPT"
    assert enriched["structure_decision"]["status"] == "ACCEPT"
    assert enriched["structure_decision"]["barrier_gate"] is None
    assert "trade_action" not in enriched
    assert "entry_status" not in enriched
    assert "signal_quality" not in enriched


def test_vrvp_stop_only_does_not_claim_structural_targets():
    setup = {
        "entry": 100.0,
        "stop": 97.0,
        "tp1": 105.0,
        "tp2": 108.0,
        "direction": "LONG",
        "tp1_is_projection": True,
        "tp2_is_projection": True,
        "target_quality": "PROJECTION_ONLY_NO_CONFIRMED_BARRIER",
    }
    vrvp = {
        "timeframe": "1D",
        "supports": [
            _causal_vrvp_level(
                97.6, "VRVP POC", "POC", 2.2,
                timeframe="1D", profile_id="support-only-stop",
            ),
        ],
        "resistances": [],
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="stock_swing"
    )

    assert enriched["vrvp_applied"] is True
    assert enriched["stop_source"] == "VRVP POC invalidation"
    assert enriched["tp1_is_projection"] is True
    assert enriched["tp2_is_projection"] is True
    assert enriched["target_quality"] == "PROJECTION_ONLY_NO_CONFIRMED_BARRIER"


def test_vrvp_support_only_preserves_existing_level_zone_target_attribution():
    setup = {
        "entry": 100.0,
        "stop": 97.0,
        "tp1": 105.0,
        "tp2": 108.0,
        "direction": "LONG",
        "tp1_source": "confirmed daily swing",
        "tp1_is_projection": False,
        "tp2_is_projection": True,
        "target_quality": "STRUCTURAL_FIRST_BARRIER",
        "nearest_barrier": {
            "side": "resistance",
            "price": 105.0,
            "source": "confirmed daily swing",
            "timeframe": "1D",
            "zone_id": "confirmed-daily-swing",
            "zone_low": 104.8,
            "zone_high": 105.2,
            "confirmed_at": "2025-01-30T20:00:00Z",
            "causal_structure_validated": True,
            "structural": True,
            "action": None,
        },
    }
    vrvp = {
        "timeframe": "1D",
        "supports": [
            _causal_vrvp_level(
                97.6, "VRVP POC", "POC", 2.2,
                timeframe="1D", profile_id="support-only-existing-target",
            ),
        ],
        "resistances": [],
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="stock_swing"
    )

    assert enriched["vrvp_applied"] is True
    assert enriched["stop_source"] == "VRVP POC invalidation"
    assert enriched["tp1"] == 105.0
    assert enriched["tp1_source"] == "confirmed daily swing"
    assert enriched["target_quality"] == "STRUCTURAL_FIRST_BARRIER"


def test_unverified_tp1_claim_without_nearest_barrier_becomes_projection_and_reject():
    setup = {
        "entry": 10.12,
        "stop": 9.865,
        "tp1": 10.4,
        "tp2": 10.9,
        "direction": "LONG",
        "tp1_source": "confirmed horizontal resistance",
        "tp1_is_projection": False,
        "tp2_is_projection": True,
        "trade_action": "TRADE_NOW",
    }
    vrvp = {"timeframe": "1D", "supports": [], "resistances": []}

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="crypto"
    )

    assert enriched["tp1"] != 10.4
    assert enriched["tp1_is_projection"] is True
    assert enriched["target_quality"] == "PROJECTION_ONLY_NO_CAUSAL_IDENTITY"
    assert enriched["unverified_tp1_claim"]["price"] == 10.4
    assert enriched["unverified_tp1_claim"]["causal_structure_validated"] is False
    assert enriched["barrier_gate"] == "CAUSAL_BARRIER_METADATA_REQUIRED"
    assert enriched["barrier_gate_active"] is True
    assert enriched["structure_status"] == "REJECT"
    assert enriched["trade_action"] == "NO_TRADE"


def test_far_unverified_tp1_claim_without_any_opposing_zone_still_rejects():
    enriched = apply_vrvp_to_trade_setup(
        {
            "entry": 100.0,
            "stop": 99.0,
            "tp1": 110.0,
            "tp2": 115.0,
            "direction": "LONG",
            "tp1_source": "claimed weekly resistance",
            "tp1_is_projection": False,
            "tp2_is_projection": True,
            "trade_action": "TRADE_NOW",
        },
        {"timeframe": "1D", "supports": [], "resistances": []},
        direction="LONG",
        asset_type="stock_swing",
    )

    assert enriched["unverified_tp1_claim"]["price"] == 110.0
    assert enriched["tp1"] != 110.0
    assert enriched["tp1_is_projection"] is True
    assert enriched["target_quality"] == "PROJECTION_ONLY_NO_CAUSAL_IDENTITY"
    assert enriched["structure_status"] == "REJECT"
    assert enriched["structure_reason"] == "tp1_marked_structural_without_causal_identity"
    assert enriched["barrier_gate"] == "CAUSAL_BARRIER_METADATA_REQUIRED"
    assert enriched["trade_action"] == "NO_TRADE"


def test_far_unverified_tp1_claim_cannot_precede_causal_vrvp_target():
    setup = {
        "entry": 100.0,
        "stop": 99.0,
        "tp1": 102.0,
        "tp2": 108.0,
        "direction": "LONG",
        "tp1_source": "confirmed intraday swing",
        "tp1_is_projection": False,
        "tp2_is_projection": True,
    }
    vrvp = {
        "timeframe": "4H",
        "supports": [],
        "resistances": [
            _causal_vrvp_level(
                104.0, "VRVP POC", "POC", 2.2,
                timeframe="4H", profile_id="farther-vrvp",
            )
        ],
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="intraday"
    )

    assert enriched["tp1"] == 104.0
    assert enriched["tp1_source"] == "VRVP POC"
    assert enriched["tp1_is_projection"] is False
    assert enriched["tp2_is_projection"] is True
    assert enriched["unverified_tp1_claim"]["price"] == 102.0
    assert enriched["structure_status"] == "REJECT"
    assert enriched["structure_reason"] == "tp1_marked_structural_without_causal_identity"
    assert enriched["trade_action"] == "NO_TRADE"


def test_existing_tp2_without_causal_zone_confirmation_is_not_structural():
    setup = {
        "entry": 100.0,
        "stop": 99.0,
        "tp1": 103.0,
        "tp2": 106.0,
        "direction": "LONG",
        "tp1_source": "confirmed 4H zone",
        "tp1_source_family": "level_zone",
        "tp1_timeframe": "4H",
        "tp1_independence_key": "level-zone:first",
        "tp1_zone_id": "first",
        "tp1_confirmed_at": "2025-01-30T20:00:00Z",
        "tp1_is_projection": False,
        "tp2_source": "unverified daily target",
        "tp2_source_family": "level_zone",
        "tp2_timeframe": "1D",
        "tp2_independence_key": "level-zone:second",
        "tp2_is_projection": False,
    }
    vrvp = {"timeframe": "4H", "supports": [], "resistances": []}

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="stock_swing"
    )

    assert enriched["tp1"] == 103.0
    assert enriched["tp2"] != 106.0
    assert enriched["tp2_is_projection"] is True
    assert enriched["tp2_projection_reason"] == "no_second_independent_structural_barrier"


def test_accepted_vrvp_barrier_never_promotes_unrelated_reject_state():
    setup = {
        "entry": 100.0,
        "stop": 99.0,
        "tp1": 105.0,
        "tp2": 108.0,
        "direction": "LONG",
        "structure_status": "REJECT",
        "structure_reason": "causal_structure_unavailable",
        "structure_decision": {
            "status": "REJECT",
            "reason": "causal_structure_unavailable",
        },
    }
    vrvp = {
        "timeframe": "4H",
        "supports": [],
        "resistances": [
            _causal_vrvp_level(
                104.0, "VRVP POC", "POC", 2.2,
                timeframe="4H", profile_id="accepted-but-unrelated-reject",
            )
        ],
    }

    enriched = apply_vrvp_to_trade_setup(
        setup, vrvp, direction="LONG", asset_type="stock_swing"
    )

    assert enriched["structure_status"] == "REJECT"
    assert enriched["structure_reason"] == "causal_structure_unavailable"
    assert enriched["structure_decision"]["status"] == "REJECT"


def test_canonical_wilder_atr_matches_indicator_reference():
    bars = []
    close = 100.0
    for index in range(36):
        open_price = close
        close = open_price + (0.6 if index % 3 else -0.35)
        bars.append({
            "o": open_price,
            "h": max(open_price, close) + 1.2 + index * 0.01,
            "l": min(open_price, close) - 0.8,
            "c": close,
            "v": 0 if index % 5 == 0 else 10_000,
        })

    normalized = [
        {"high": bar["h"], "low": bar["l"], "close": bar["c"]}
        for bar in bars
    ]
    expected, _ = calculate_atr_14(normalized)

    assert round(calculate_wilder_atr(bars), 4) == expected


def test_canonical_wilder_atr_requires_full_period_and_rejects_bad_bars():
    short_history = [
        {"high": 10.5, "low": 9.5, "close": 10.0}
        for _ in range(14)
    ]
    assert calculate_wilder_atr(short_history) == 0.0

    enough_history = short_history + [
        {"high": 11.0, "low": 9.0, "close": 10.5},
        {"high": 1.0, "low": 2.0, "close": 1.5},
    ]
    assert calculate_wilder_atr(enough_history) > 0


def test_structural_level_callsites_use_canonical_atr_before_fallbacks():
    root = Path(__file__).resolve().parent
    api_source = (root / "api.py").read_text(encoding="utf-8")
    scanner_source = (root / "modules" / "scanners.py").read_text(encoding="utf-8")
    listing_source = (root / "modules" / "new_listing_scanner.py").read_text(encoding="utf-8")

    assert api_source.count("calculate_wilder_atr(") >= 5
    assert "calculate_wilder_atr(_session_bars" in scanner_source
    assert "calculate_wilder_atr(\n        completed_vrvp_bars" in listing_source
    assert 'timeframe="1H"' in listing_source
    assert 'timeframe="1h_listing"' not in listing_source
    assert "atr=max(0.00000001, ath - current" not in listing_source


def test_root_scanner_copies_are_isolated_from_production_modules():
    root = Path(__file__).resolve().parent
    listing_shim = (root / "new_listing_scanner.py").read_text(encoding="utf-8")
    volume_stub = (root / "volume_profile.py").read_text(encoding="utf-8")

    assert "from modules.new_listing_scanner import *" in listing_shim
    assert len(listing_shim.splitlines()) < 20
    assert "raise ImportError(" in volume_stub
    assert "modules.vrvp_levels" in volume_stub
    assert len(volume_stub.splitlines()) < 20


@pytest.mark.parametrize(
    ("trade_horizon", "scanner_name", "asset_class", "expected"),
    [
        ("swing", "stock_strategy", "stock", 1.5),
        ("intraday", "orb", "stock", 0.4),
        ("swing", "early_movers", "crypto", 1.2),
        ("position", "turtle", "stock", 2.0),
    ],
)
def test_minimum_stop_distance_uses_horizon_and_asset_noise_floor(
    trade_horizon, scanner_name, asset_class, expected
):
    result = minimum_stop_distance(
        100.0,
        trade_horizon=trade_horizon,
        scanner_name=scanner_name,
        asset_class=asset_class,
    )
    assert result["distance"] == pytest.approx(expected)


def test_minimum_stop_distance_uses_largest_atr_or_spread_floor():
    result = minimum_stop_distance(
        100.0,
        atr=10.0,
        spread_pct=2.0,
        trade_horizon="swing",
        scanner_name="stock_strategy",
        asset_class="stock",
    )
    assert result["distance"] == pytest.approx(4.5)
    assert result["components"]["atr_floor"] == pytest.approx(4.5)
