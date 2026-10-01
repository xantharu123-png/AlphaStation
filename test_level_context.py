"""Pure offline tests: no API, providers, configuration or SMTP imports."""
from copy import deepcopy
from datetime import datetime, timezone
import ast
from functools import lru_cache
import json
from pathlib import Path

import pytest

from modules.level_context import build_level_context


CUTOFF = "2026-09-29T20:00:00Z"
CONFIRMED = "2026-09-28T20:00:00Z"


def _native(side="LONG"):
    prices = (100, 94, 114, 126) if side == "LONG" else (100, 106, 86, 74)
    plan = dict(direction=side, entry=prices[0], stop=prices[1], tp1=prices[2], tp2=prices[3],
                level_model="causal_level_zones_v1+invalidation_first_v2", rr=2.4,
                accepted=False, barrier_gate_active=True, trade_action="WAIT_FOR_BREAK_RECLAIM")
    for prefix, price in zip(("stop", "tp1", "tp2"), prices[1:]):
        # The stop's buffered execution price is not its physical S/R boundary.
        low, high = (95, 96) if prefix == "stop" and side == "LONG" else (
            (104, 105) if prefix == "stop" else (price - .5, price + .5))
        plan.update({f"{prefix}_source": "confirmed horizontal invalidation" if prefix == "stop" else "confirmed opposing zone",
                     f"{prefix}_source_family": "level_zone", f"{prefix}_timeframe": "1D/1W",
                     f"{prefix}_zone_id": f"lz_{prefix}", f"{prefix}_zone_low": low, f"{prefix}_zone_high": high,
                     f"{prefix}_independence_key": f"level_zone:1D/1W:lz_{prefix}",
                     f"{prefix}_confirmed_at": CONFIRMED, f"{prefix}_data_cutoff_at": CUTOFF,
                     f"{prefix}_causal_structure_validated": True, f"{prefix}_is_projection": False})
    return plan


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_native_zone_provenance_and_tp2_identity_are_preserved_without_release(side):
    plan = _native(side)
    before = deepcopy(plan)
    context = build_level_context(plan, timeframe="8H", as_of=CUTOFF)
    assert plan == before
    assert context["trade_release_asserted"] is False
    assert "accepted" not in context and "trade_action" not in context
    assert context["entry"] == plan["entry"]
    assert context["timeframe"] == "8H"
    assert context["stop"]["evidence_status"] == "confirmed"
    assert context["stop"]["price"] == plan["stop"]
    assert context["stop"]["zone_low"] == plan["stop_zone_low"]
    assert context["targets"]["tp2"]["zone_id"] == "lz_tp2"
    assert context["targets"]["tp2"]["independence_key"] == plan["tp2_independence_key"]
    assert context["targets"]["tp2"]["timeframe"] == "1D/1W"
    assert context["targets"]["tp2"]["data_cutoff_at"] == CUTOFF
    assert len(context["support_levels"]) == (1 if side == "LONG" else 2)
    assert len(context["resistance_levels"]) == (2 if side == "LONG" else 1)
    json.dumps(context, allow_nan=False)


@pytest.mark.parametrize("source", ["measured move 2.5R (turtle)", "161_8_range_projection",
                                    "ATR target", "Fibonacci extension", "range_extension", "fib_161_8_target"])
def test_projection_label_never_promotes_even_with_claimed_confirmed_metadata(source):
    plan = _native()
    plan["tp2_source"] = source
    context = build_level_context(plan, as_of=CUTOFF)
    assert context["targets"]["tp2"]["evidence_status"] == "projection_only"
    assert context["targets"]["tp2"]["causal_structure_validated"] is False
    assert not any(item["zone_id"] == "lz_tp2" for item in context["resistance_levels"])


def test_fibonacci_stop_reference_is_not_promoted_as_horizontal_support():
    plan = _native()
    plan["stop_source"] = "fib_61_8_retest_invalidation"
    context = build_level_context(plan, as_of=CUTOFF)
    assert context["stop"]["evidence_status"] == "projection_only"
    assert context["stop"]["price"] == plan["stop"]
    assert not context["support_levels"]


@pytest.mark.parametrize("model,sources,family", [
    ("bi_shared_structure_v3", ("range_high_retest_invalidation", "range_extension", "range_extension"), "range_geometry"),
    ("orb_range_projection", ("OR midpoint tactical stop", "OR measured move 1.0x range", "OR measured move 1.5x range"), "opening_range_geometry"),
    ("turtle_r_multiple_v1", ("2 ATR turtle stop", "measured move 1.5R (turtle)", "measured move 2.5R (turtle)"), "donchian_geometry"),
])
def test_own_geometry_is_not_confirmed_horizontal_sr(model, sources, family):
    plan = dict(direction="LONG", Entry=100, StopLoss=95, TP1=110, TP2=120, level_model=model,
                stop_source=sources[0], tp1_source=sources[1], tp2_source=sources[2], RangeHigh=99, RangeLow=94)
    result = build_level_context(plan, timeframe="1D", as_of=CUTOFF)
    assert result["source_family"] == "unknown"
    assert result["geometry_family"] == family
    assert not result["support_levels"] and not result["resistance_levels"]
    assert result["stop"]["evidence_status"] == "native_geometry"
    assert all(item["evidence_status"] == "projection_only" for item in result["targets"].values())
    assert {item["price"] for item in result["geometry_levels"]} >= {99, 94, 95}


@pytest.mark.parametrize("field,value,status,reason", [
    ("tp2_data_cutoff_at", None, "causality_unknown", "completion_or_cutoff_unknown"),
    ("tp2_confirmed_at", "2026-09-30T20:00:00Z", "unverified", "future_evidence_or_cutoff"),
    ("tp2_data_cutoff_at", "2026-09-30T20:00:00Z", "unverified", "future_evidence_or_cutoff"),
    ("tp2_confirmed_at", "2026-09-28", "causality_unknown", "completion_or_cutoff_unknown"),
    ("tp2_causal_structure_validated", False, "unverified", "producer_confirmation_missing"),
    ("tp2_timeframe", None, "unverified", "zone_identity_or_timeframe_missing"),
    ("tp2_zone_low", None, "unverified", "physical_zone_bounds_invalid_or_missing"),
])
def test_missing_or_future_causality_is_not_repaired_by_display_context(field, value, status, reason):
    plan = _native()
    plan[field] = value
    result = build_level_context(plan, timeframe="1D", as_of=CUTOFF)
    assert result["targets"]["tp2"]["evidence_status"] == status
    assert result["targets"]["tp2"]["reason"] == reason
    assert result["targets"]["tp2"]["data_cutoff_at"] == plan["tp2_data_cutoff_at"]


def test_same_native_zone_deduplicates_but_distinct_timeframes_do_not():
    plan = _native()
    plan["nearest_barrier"] = dict(price=114, side="resistance", source="confirmed opposing zone",
                                   source_family="level_zone", timeframe="1D/1W", zone_id="lz_tp1",
                                   zone_low=113.5, zone_high=114.5, confirmed_at=CONFIRMED,
                                   data_cutoff_at=CUTOFF, causal_structure_validated=True)
    result = build_level_context(plan, as_of=CUTOFF)
    matching = [item for item in result["resistance_levels"] if item["zone_id"] == "lz_tp1"]
    assert len(matching) == 1
    assert {ref["purpose"] for ref in matching[0]["references"]} == {"tp1", "nearest_barrier"}
    plan["nearest_barrier"]["timeframe"] = "4H"
    assert len(build_level_context(plan, as_of=CUTOFF)["resistance_levels"]) == 3


def test_later_quotes_never_move_native_bounds_or_replace_missing_entry():
    plan = _native()
    original = build_level_context({"trade_setup": plan, "price": 100}, as_of=CUTOFF)
    later = build_level_context({"trade_setup": plan, "price": 140, "bid": 139.8, "ask": 140.2}, as_of=CUTOFF)
    assert later == original
    del plan["entry"]
    assert build_level_context({"trade_setup": plan, "price": 140})["entry"] is None


def test_conflicting_geometry_for_same_identity_downgrades_targets_and_sr():
    plan = _native()
    plan["nearest_barrier"] = dict(price=114, side="resistance", source="confirmed opposing zone",
                                   source_family="level_zone", timeframe="1D/1W", zone_id="lz_tp1",
                                   zone_low=110, zone_high=114.5, confirmed_at=CONFIRMED,
                                   data_cutoff_at=CUTOFF, causal_structure_validated=True)
    result = build_level_context(plan, as_of=CUTOFF)
    assert result["targets"]["tp1"]["evidence_status"] == "unverified"
    assert result["targets"]["tp1"]["causal_structure_validated"] is False
    assert not any(item["zone_id"] == "lz_tp1" for item in result["resistance_levels"])
    bad = [item for item in result["unverified_levels"] if item["zone_id"] == "lz_tp1"]
    assert len(bad) == 1
    assert bad[0]["reason"] == "conflicting_bounds_for_same_zone"
    assert len(bad[0]["conflicting_bounds"]) == 2


def test_penny_crypto_source_labels_and_quality_do_not_substitute_provenance():
    for model in ("penny_5m_execution_multitimeframe_structure_v2", "crypto_structure_first_v2"):
        plan = dict(entry=100, stop=95, tp1=110, tp2=120, direction="LONG", model=model,
                    stop_source="5m swing_low", tp1_source="observed_24h_high_liquidity",
                    tp2_source="structural resistance", target_quality="STRUCTURAL")
        result = build_level_context(plan, as_of=CUTOFF)
        assert not result["support_levels"] and not result["resistance_levels"]
        assert result["targets"]["tp1"]["source"] == plan["tp1_source"]
        assert result["targets"]["tp1"]["source_family"] == "unknown"
        assert result["targets"]["tp1"]["evidence_status"] == "unverified"


def _snapshot():
    return {"model": "causal_level_zones_v1", "as_of": CUTOFF, "current_price": 100,
            "zones": [{"zone_id": "lz_history", "lower": 94, "upper": 96, "reference": 95,
                       "side_at_reference": "support", "confirmed_at": CONFIRMED,
                       "projection_only": False, "independent_structural_sources": 1,
                       "sources": ["confirmed_daily_pivot_low"], "evidence": [
                           {"source_family": "pivot", "source_name": "confirmed_daily_pivot_low",
                            "timeframe": "1D", "lower": 94, "upper": 96,
                            "observed_at": "2026-09-25T20:00:00Z", "confirmed_at": CONFIRMED,
                            "data_cutoff_at": CUTOFF, "projection_only": False}]}]}


def test_canonical_snapshot_keeps_physical_zone_and_does_not_use_new_quote():
    snap = _snapshot()
    before = deepcopy(snap)
    result = build_level_context({"price": 150}, snapshot=snap, timeframe="8H", as_of=CUTOFF)
    assert snap == before
    assert len(result["support_levels"]) == 1
    assert result["support_levels"][0]["zone_low"] == 94
    assert result["support_levels"][0]["zone_high"] == 96
    assert result["support_levels"][0]["price"] is None
    assert result["support_levels"][0]["timeframe"] == "1D"
    assert result["entry"] is None
    snap["zones"][0]["evidence"][0]["confirmed_at"] = "2026-09-30T20:00:00Z"
    assert not build_level_context({}, snapshot=snap, as_of=CUTOFF)["support_levels"]


def test_snapshot_without_original_cutoff_remains_causality_unknown():
    snap = _snapshot()
    del snap["as_of"]
    result = build_level_context({}, snapshot=snap, as_of=CUTOFF)
    assert not result["support_levels"]
    assert result["unverified_levels"][0]["evidence_status"] == "causality_unknown"
    assert result["unverified_levels"][0]["data_cutoff_at"] is None


def test_projection_only_snapshot_does_not_become_confirmed_zone():
    snap = _snapshot()
    snap["zones"][0]["projection_only"] = True
    result = build_level_context({}, snapshot=snap, as_of=CUTOFF)
    assert not result["support_levels"]
    assert result["unverified_levels"][0]["evidence_status"] == "projection_only"


def test_fibonacci_member_cannot_be_promoted_by_inconsistent_aggregate_flag():
    snap = _snapshot()
    snap["zones"][0]["evidence"][0]["source_family"] = "fibonacci"
    snap["zones"][0]["evidence"][0]["projection_only"] = False
    result = build_level_context({}, snapshot=snap, as_of=CUTOFF)
    assert not result["support_levels"]
    assert result["unverified_levels"][0]["evidence_status"] == "projection_only"


def test_real_vrvp_prefix_evidence_keeps_original_family_timeframe_and_bounds():
    plan = _native()
    for prefix in ("tp1", "tp2"):
        plan[f"{prefix}_source_family"] = "vrvp"
        plan[f"{prefix}_timeframe"] = "4H"
        plan[f"{prefix}_source"] = "VRVP HVN resistance"
    result = build_level_context(plan, timeframe="5m", as_of=CUTOFF)
    assert result["source_family"] == "mixed"
    assert result["source_families"] == ["level_zone", "vrvp"]
    assert result["timeframe"] == "5m"
    assert result["timeframes"] == ["1D/1W", "4H"]
    assert result["targets"]["tp2"]["source_family"] == "vrvp"
    assert result["targets"]["tp2"]["timeframe"] == "4H"
    assert result["targets"]["tp2"]["zone_high"] == plan["tp2_zone_high"]
    assert result["targets"]["tp2"]["evidence_status"] == "confirmed"


def test_invalid_execution_price_does_not_receive_confirmation_from_zone_metadata():
    plan = _native()
    plan["tp2"] = float("nan")
    result = build_level_context(plan, as_of=CUTOFF)
    assert result["targets"]["tp2"]["evidence_status"] == "unverified"
    assert result["targets"]["tp2"]["reason"] == "execution_price_invalid_or_missing"
    assert not any(item["zone_id"] == "lz_tp2" for item in result["resistance_levels"])


def test_no_numbers_or_confirmation_are_created_for_empty_or_invalid_plan():
    assert build_level_context(None)["availability"] == "unavailable"
    result = build_level_context(dict(price=100, TP1=float("nan"), StopLoss=True, direction="LONG"))
    assert result["entry"] is None
    assert result["targets"]["tp1"]["price"] is None
    assert result["stop"]["price"] is None
    assert not result["support_levels"] and not result["resistance_levels"]
    json.dumps(result, allow_nan=False)


def test_aware_datetime_cutoff_accepted_but_naive_cutoff_not_guessed():
    aware = datetime(2026, 9, 29, 20, tzinfo=timezone.utc)
    assert build_level_context(_native(), as_of=aware)["targets"]["tp2"]["evidence_status"] == "confirmed"
    assert build_level_context(_native(), as_of=aware.replace(tzinfo=None))["targets"]["tp2"]["evidence_status"] == "causality_unknown"


def test_actual_crypto_profile_levels_do_not_invent_missing_target_identity():
    level = dict(price=114, lower=113.5, upper=114.5, source="vrvp_hvn_resistance",
                 source_family="vrvp", timeframe="4H", zone_id="profile_hvn1",
                 confirmed_at=CONFIRMED, data_cutoff_at=CUTOFF, causal_structure_validated=True)
    plan = dict(entry=100, stop=95, tp1=114, tp2=126, direction="LONG",
                tp1_source="vrvp_hvn_resistance", tp1_is_projection=False,
                vrvp_levels=dict(profile_method="ohlcv_profile", causal_structure_validated=True,
                                 causal_completion_verified=True, supports=[], resistances=[level]))
    result = build_level_context(plan, as_of=CUTOFF)
    assert len(result["resistance_levels"]) == 1
    assert result["resistance_levels"][0]["source_family"] == "vrvp"
    assert result["targets"]["tp1"]["evidence_status"] == "unverified"
    assert result["targets"]["tp1"]["zone_id"] is None
    plan["vrvp_levels"]["causal_completion_verified"] = False
    assert not build_level_context(plan, as_of=CUTOFF)["resistance_levels"]


@lru_cache(maxsize=1)
def _actual_row_decorator():
    """Compile only the real pure row function; never import/initialize api.py.

    Non-context dependencies are isolated no-op/read-only stubs. These cases
    test the real output hook, not provider calls or full execution policy.
    """
    source_path = Path(__file__).resolve().parent / "api.py"
    parsed = ast.parse(source_path.read_text(encoding="utf-8"))
    function = next(node for node in parsed.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_decorate_scan_results")
    namespace = {
        "build_level_context": build_level_context,
        "_get_market_context_snapshot": lambda: {},
        "STOCK_SCANNER_ASSET_GUARD_NAMES": set(),
        "_STOCK_RESULT_TRADE_STATE_SCANNERS": set(),
        "is_elliott_pattern_context": lambda *args, **kwargs: False,
        "_alert_float": lambda value: float(value) if value is not None else None,
        "_strategy_score_to_grade": lambda score: "A",
        "_turtle_score_cap": lambda score, *args: (score, []),
        "SCAN_DATA_SOURCES": {},
        "RISK_POLICY": {"preferred_min_rr": 1.5, "min_rvol": 1.0},
        "_attach_trade_health": lambda *args: {"decision_label": "unchanged", "health_score": 0,
                                               "fakeout_risk": "unknown", "chase_risk": "unknown",
                                               "warnings": [], "exclusion_reasons": []},
        "_apply_scanner_result_trade_state": lambda *args: None,
        "_apply_trade_barrier_gate": lambda *args: None,
        "_apply_trade_health_final_signal": lambda *args: None,
    }
    isolated = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                               function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(isolated), str(source_path), "exec"), namespace)
    return namespace["_decorate_scan_results"]


def test_actual_common_row_hook_maps_native_zone_proof_without_mutating_plan():
    plan = _native()
    row = {**plan, "trade_setup": plan, "score": 86, "grade": "A"}
    before = deepcopy(row)
    result = _actual_row_decorator()([row], "strategy_scan", 60)[0]
    assert row == before
    assert result["trade_setup"] == before["trade_setup"]
    assert result["level_context"]["targets"]["tp2"]["evidence_status"] == "confirmed"
    assert result["level_context"]["targets"]["tp2"]["zone_id"] == "lz_tp2"
    for key in ("entry", "stop", "tp1", "tp2", "score", "accepted", "trade_action", "barrier_gate_active"):
        assert result[key] == before[key]


def test_actual_common_row_hook_is_additive_for_mixed_scanner_families():
    decorator = _actual_row_decorator()
    for scanner, model, target_source in (
        ("bi_long", "bi_shared_structure_v3", "range_extension"),
        ("orb", "orb_range_projection", "OR measured move 1.0x range"),
        ("turtle", "turtle_r_multiple_v1", "measured move 1.5R (turtle)"),
        ("penny", "penny_5m_execution_multitimeframe_structure_v2", "daily swing_high"),
        ("early_movers", "crypto_structure_first_v2", "161_8_range_projection"),
        ("new_listing", "new_listing_ath_projection", "ath_dump_projection"),
    ):
        row = dict(direction="LONG", entry=100, stop=95, tp1=110, tp2=120, score=70, grade="A",
                   accepted=False, trade_action="WAIT_FOR_RETEST", execution_trigger_ok=False,
                   barrier_gate_active=True, level_model=model, stop_source="native invalidation",
                   tp1_source=target_source, tp2_source=target_source)
        before = deepcopy(row)
        result = decorator([row], scanner, 60)[0]
        assert row == before
        for key in before:
            assert result[key] == before[key], (scanner, key)
        assert result["level_context"]["trade_release_asserted"] is False
        assert not result["level_context"]["support_levels"]
        assert not result["level_context"]["resistance_levels"]
