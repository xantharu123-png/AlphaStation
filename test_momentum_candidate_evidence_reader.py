"""Isolated hand-written fixtures for the standalone, read-only Momentum reader.

No application/API imports, actual service access, providers, SMTP or credentials.
Only the reader module is imported; process identity and I/O races are simulated.
"""
import ast
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


READER_PATH = Path(__file__).parent / "scripts" / "collect_momentum_candidate_evidence.py"
WRAPPER_PATH = READER_PATH.with_suffix(".ps1")
STAMP = "2026-10-07T20:00:00Z"
ZONE = "lz_0123456789abcdef"
PRIVATE = "never-export-secret@example.invalid"


@pytest.fixture
def reader():
    assert READER_PATH.is_file()
    spec = importlib.util.spec_from_file_location("momentum_candidate_reader_fixture", READER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate(ticker="UVE"):
    """Native serializer-shaped evidence, not constructed with reader helpers."""
    evidence = {
        "source_family": "horizontal_swing", "source_name": "confirmed_swing_high",
        "timeframe": "1D", "lower": 45.30, "upper": 45.30, "strength": 1.4,
        "observed_at": "2026-09-30T20:00:00Z", "confirmed_at": STAMP,
        "data_cutoff_at": STAMP, "projection_only": False,
        "independence_key": "horizontal_swing:1D",
        "provenance": {"pivot_index": 20, "confirmation_bar_index": 22, "pivot_left": 2,
                       "pivot_right": 2, "touch_count": 1, "role_hint": "resistance",
                       "session_closed_at": STAMP, "private_note": PRIVATE},
        "secret": PRIVATE,
    }
    history = {"model": "connected_role_boundary_v2", "zone_id": ZONE,
               "lower": 45.27, "upper": 45.33, "membership_confirmed_at": STAMP,
               "confirmed_at_by_direction": {"LONG": "2026-10-06T20:00:00Z", "private": PRIVATE}}
    proof = {
        "model": "break_confirmed_optional_retest_v1", "state": "BREAK_CONFIRMED",
        "reason": "completed_break_close_confirmed_retest_optional", "direction": "LONG",
        "zone_id": ZONE, "boundary": 45.33, "zone_confirmed_at": "2026-10-06T20:00:00Z",
        "as_of": STAMP, "break_closed_at": STAMP, "last_completed_at": STAMP,
        "last_completed_close": 45.4, "timeframe": "1D", "hold_bars_required": 0,
        "hold_bars_observed": 0, "completed_bars_used": 1, "retest_required": False,
        "retest_observed": False, "reclaim_history": history, "email": PRIVATE,
    }
    zone = {
        "zone_id": ZONE, "lower": 45.27, "upper": 45.33, "reference": 45.30,
        "strength": 1.4, "touch_count": 1, "independent_sources": 1,
        "independent_structural_sources": 1, "side_at_reference": "resistance",
        "sources": ["confirmed_swing_high"], "origin_roles": ["resistance"],
        "projection_only": False, "confirmed_at": STAMP, "break_state": "intact",
        "quality_flags": ["breakout_confirmed_without_retest"], "evidence": [evidence],
        "break_reclaim_evidence": proof, "reclaim_history": history, "account": PRIVATE,
    }
    barrier = {
        "zone_id": ZONE, "zone_low": 45.27, "zone_high": 45.33, "price": 45.27,
        "side": "resistance", "source": "confirmed_swing_high (1D/1W/4H)",
        "timeframe": "1D/1W/4H", "confirmed_at": STAMP, "data_cutoff_at": STAMP,
        "distance_r": .02941176470588, "strength": 1.4, "independent_sources": 1,
        "reclaim_boundary": 45.33, "overlapping": False, "reclaimed": False,
        "action": "BREAK_RECLAIM_REQUIRED", "structural": True,
        "causal_structure_validated": True, "source_family": "level_zone",
        "independence_key": "level_zone:1D/1W/4H:" + ZONE,
        "break_reclaim": proof, "reclaim_history": history, "subject": PRIVATE,
    }
    return {
        "ticker": ticker, "Ticker": ticker, "Strategy": "Momentum Breakout Long",
        "strategy": "Momentum Breakout Long", "score": 45, "Score": 45, "Setup_Score": 91,
        "price": 45.25, "Preis": 45.25, "MedianDollarVol20": 1_999_999.125,
        "median_dollar_volume_20d": 1_999_999.125, "History_OK": True, "History_Bars": 70,
        "Day_High": 45.2591, "day_high": 45.2591, "TP1": 45.27, "tp1": 45.27,
        "Breakout_Continuation_Score": 86.125, "analysis_as_of": STAMP,
        "stock_swing_contract_version": 1, "stock_swing_mode": "completed_daily_swing",
        "swing_analysis_session": "2026-10-07", "swing_reference_close": 45.25,
        "swing_data_delay_seconds": 900, "swing_timeframe": "1D", "trade_horizon": "swing",
        "scan_price_observed_at": STAMP, "scan_price_source": "polygon_completed_1d_swing",
        "price_observed_at": STAMP, "price_source": "polygon_completed_1d_swing",
        "price_mode": "swing_reference_close", "price_session": "COMPLETED_US_SESSION",
        "fill_evidence_verified": False, "native_plan_status": "built",
        "native_plan_reason": "native_structure_plan", "nearest_barrier": barrier,
        "native_plan_diagnostics": {"status": "built", "reason": "native_structure_plan",
                                    "barrier": barrier, "secret": PRIVATE},
        "trade_setup": {"entry": 45.25, "stop": 44.57, "tp1": 45.27, "tp2": 47.1,
                        "risk": .68, "rr_tp1": .02941176470588, "rr_tp2": 2.72,
                        "atr": .21, "direction": "LONG", "structure_status": "WAIT_BREAK_RECLAIM",
                        "structure_reason": "first_opposing_barrier_before_minimum_rr",
                        "barrier_gate": "BREAK_RECLAIM_REQUIRED", "barrier_gate_active": True,
                        "stop_zone_id": ZONE, "tp1_zone_id": ZONE, "tp1_confirmed_at": STAMP,
                        "stop_source": "confirmed_swing_low (1D) invalidation",
                        "tp1_source": "confirmed_swing_high (1D/1W/4H)", "nearest_barrier": barrier,
                        "structure_decision": {"model": "structure_decision_v1", "status": "WAIT_BREAK_RECLAIM",
                                               "reason": "first_opposing_barrier_before_minimum_rr", "entry": 45.25,
                                               "stop": 44.57, "risk": .68, "barrier_r": .02941176470588,
                                               "target1": 45.27, "nearest_barrier": zone}, "warnings": [PRIVATE]},
        "level_structure": {"model": "causal_level_zones_v2", "symbol": ticker,
                            "asset_class": "stock", "horizon": "swing", "as_of": STAMP,
                            "current_price": 45.25, "zones": [zone], "atr_by_timeframe": {"1D": .21},
                            "completed_bar_counts": {"1D": 70, "4H": 40}, "quality_flags": []},
        "email": PRIVATE, "provider_key": PRIVATE, "user_id": PRIVATE,
    }


def payload():
    return {"cache_version": 20, "timestamp": 1791403500.0, "partial": False,
            "results": [{"ticker": "OTHER", "email": PRIVATE}, candidate()],
            "diagnostics": {"coverage": "complete", "secret": PRIVATE}}


def write_cache(tmp_path, data=None):
    path = tmp_path / "strategy_momentum_breakout_long_cache.json"
    path.write_text(json.dumps(payload() if data is None else data), encoding="utf8")
    return path


def project(reader, data=None):
    return reader.project_payload(payload() if data is None else data, {"sha256": "0" * 64})


def projected(result, ticker="UVE"):
    return next(row for row in result["candidates"] if row["ticker"] == ticker)


def test_exact_raw_numbers_native_identity_hash_and_no_other_rows(reader, tmp_path):
    path = write_cache(tmp_path)
    result = reader.project_payload(*reader.read_cache(path))
    item = projected(result)
    assert result["status"] == "ok" and result["read_only"] is True
    assert [row["ticker"] for row in result["candidates"]] == ["RELL", "UVE", "NECB", "GKOS"]
    assert result["cache"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["cache"]["size_bytes"] == path.stat().st_size
    assert result["cache"]["rows_total"] == 2 and item["row_index"] == 1
    assert item["scanner_numbers"]["MedianDollarVol20"] == 1_999_999.125
    assert item["scanner_numbers"]["median_dollar_volume_20d"] == 1_999_999.125
    assert item["scanner_numbers"]["Day_High"] == 45.2591
    assert item["scanner_numbers"]["TP1"] == 45.27
    assert item["scanner_numbers"]["History_Bars"] == 70
    assert item["scanner_booleans"]["History_OK"] is True
    assert item["trade_setup"]["risk"] == .68
    assert item["nearest_barrier"]["zone_id"] == ZONE
    assert item["nearest_barrier"]["source"] == "confirmed_swing_high (1D/1W/4H)"
    zone = item["level_structure"]["zones"][0]
    assert zone["lower"] == 45.27 and zone["upper"] == 45.33
    assert zone["evidence"][0]["provenance"]["pivot_index"] == 20
    assert zone["break_reclaim_evidence"]["retest_required"] is False
    assert zone["reclaim_history"]["confirmed_at_by_direction"]["LONG"] == "2026-10-06T20:00:00+00:00"
    assert item["native_plan_diagnostics"]["barrier"]["zone_id"] == ZONE
    assert result["raw_daily_prefix"]["provider_fetch_performed"] is False
    serialized = json.dumps(result, allow_nan=False)
    assert PRIVATE not in serialized and "OTHER" not in serialized


def test_each_exact_alias_selects_only_hardcoded_ticker(reader):
    for ticker in reader.TICKERS:
        for field in ("ticker", "Ticker"):
            row = candidate(ticker)
            row.pop("ticker" if field == "Ticker" else "Ticker")
            result = project(reader, {"results": [row]})
            assert projected(result, ticker)["status"] == "ok"
            assert all(item["status"] == "not_found" for item in result["candidates"] if item["ticker"] != ticker)


def test_no_fuzzy_ticker_selection_or_empty_cache_global_failure(reader):
    for symbol in ("uve", " UVE", "UVE ", "UVE.A", PRIVATE):
        row = candidate()
        row.update(ticker=symbol, Ticker=symbol)
        result = project(reader, {"results": [row]})
        assert result["status"] == "ok"
        assert all(item["status"] == "not_found" for item in result["candidates"])
        assert PRIVATE not in json.dumps(result)


def test_one_duplicate_does_not_erase_other_selected_candidates(reader):
    result = project(reader, {"results": [candidate("RELL"), candidate(), candidate()]})
    assert projected(result, "RELL")["status"] == "ok"
    assert projected(result)["status"] == "not_unique"
    assert projected(result)["row_indices"] == [1, 2]
    assert projected(result, "GKOS")["status"] == "not_found"


def test_ticker_conflict_never_leaks_unknown_alias_or_misattributed_plan(reader):
    for counterpart in ("RELL", "OTHER", PRIVATE):
        row = candidate()
        row["Ticker"] = counterpart
        result = project(reader, {"results": [row]})
        item = projected(result)
        assert item["status"] == "ticker_alias_conflict"
        assert "trade_setup" not in item
        assert set(item["ticker_aliases"].values()) <= set(reader.TICKERS)
        assert PRIVATE not in json.dumps(result) and "OTHER" not in json.dumps(result)


def test_conflicting_numeric_and_structure_aliases_are_not_normalized_away(reader):
    data = payload()
    row = data["results"][1]
    row.update(Score=46, median_dollar_volume_20d=2_000_000.125, tp1=45.28)
    row["Level_Structure"] = copy.deepcopy(row["level_structure"])
    row["Level_Structure"]["zones"][0]["upper"] = 45.34
    row["trade_setup"]["TP1"] = 45.29
    result = projected(project(reader, data))
    assert set(result["alias_conflicts"]) == {"score", "median_dollar_volume_20d", "tp1", "level_structure"}
    assert result["scanner_numbers"]["score"] == 45 and result["scanner_numbers"]["Score"] == 46
    assert result["scanner_numbers"]["MedianDollarVol20"] == 1_999_999.125
    assert result["scanner_numbers"]["median_dollar_volume_20d"] == 2_000_000.125
    assert result["level_structure"]["zones"][0]["upper"] == 45.33
    assert result["Level_Structure"]["zones"][0]["upper"] == 45.34
    assert result["trade_setup"]["alias_conflicts"] == ["tp1"]
    assert result["trade_setup"]["TP1"] == 45.29


def test_explicit_null_aliases_and_barrier_clear_are_preserved(reader):
    row = candidate()
    row["Level_Structure"] = None
    row["nearest_barrier"] = None
    row["barrier_gate"] = None
    row["trade_setup"]["nearest_barrier"]["action"] = None
    item = projected(project(reader, {"results": [row]}))
    assert item["Level_Structure"] is None
    assert "level_structure" in item and "level_structure" in item["alias_conflicts"]
    assert item["nearest_barrier"] is None and item["barrier_gate"] is None
    assert item["trade_setup"]["nearest_barrier"]["action"] is None


def test_actual_final_vrvp_decision_preserves_flat_barrier_and_selected_level_evidence(reader):
    # Shape from vrvp_levels._trade_level_evidence and final decision sync;
    # this is a hand-written stored fixture, not an application import.
    row = candidate()
    vrvp_zone = "vrvp-zone-0123456789abcdef0123"
    profile = "vrvp-profile-0123456789abcdef0123"
    flat = {"price": 45.27, "zone_id": vrvp_zone, "zone_low": 45.2612345,
            "zone_high": 45.3309876, "side": "resistance", "source": "VRVP HVN mid",
            "source_family": "vrvp", "profile_id": profile, "independence_key": profile,
            "timeframe": "1D", "confirmed_at": STAMP, "data_cutoff_at": STAMP,
            "causal_structure_validated": True, "distance_basis": "zone_low",
            "entry_boundary": 45.2612345, "entry_inside_zone": False, "distance_r": .02,
            "minimum_reward": 1.02, "minimum_rr": 1.5, "below_minimum_reward": True,
            "action": "BREAK_RECLAIM_REQUIRED", "private": PRIVATE}
    level = {"price": 45.27, "source": "VRVP HVN mid", "source_family": "vrvp",
             "timeframe": "1D", "zone_id": vrvp_zone, "zone_low": 45.2612345,
             "zone_high": 45.3309876, "confirmed_at": STAMP, "data_cutoff_at": STAMP,
             "independence_key": profile, "causal_structure_validated": True,
             "is_projection": False, "private": PRIVATE}
    decision = {"model": "structure_decision_v1", "status": "WAIT_BREAK_RECLAIM",
                "reason": "first_opposing_barrier_before_minimum_reward", "entry": 45.25,
                "stop": 44.57, "risk": .68, "target1": 45.27, "target2": 47.1,
                "direction": "LONG", "geometry_updated_by": "vrvp_trade_setup", "entry_eligible": False,
                "nearest_barrier": flat, "stop_evidence": dict(level, price=44.57),
                "target1_evidence": level,
                "target2_evidence": {"price": 47.1, "source": "projection fallback (no second independent structural barrier)",
                                     "is_projection": True, "causal_structure_validated": False, "secret": PRIVATE}}
    row["structure_decision"] = decision
    row["trade_setup"]["structure_decision"] = decision
    row["trade_setup"]["level_model"] = "causal_level_zones_v2+invalidation_first_v2+vrvp"
    item = projected(project(reader, {"results": [row]}))
    for projected_decision in (item["structure_decision"], item["trade_setup"]["structure_decision"]):
        assert projected_decision["target2"] == 47.1
        assert projected_decision["geometry_updated_by"] == "vrvp_trade_setup"
        assert projected_decision["nearest_barrier"]["price"] == 45.27
        assert projected_decision["nearest_barrier"]["zone_low"] == 45.2612345
        assert projected_decision["nearest_barrier"]["zone_id"] == vrvp_zone
        assert projected_decision["nearest_barrier"]["profile_id"] == profile
        assert projected_decision["nearest_barrier"]["distance_basis"] == "zone_low"
        assert projected_decision["target1_evidence"]["independence_key"] == profile
        assert projected_decision["target1_evidence"]["zone_high"] == 45.3309876
        assert projected_decision["stop_evidence"]["price"] == 44.57
        assert projected_decision["target2_evidence"]["is_projection"] is True
        assert projected_decision["target2_evidence"]["causal_structure_validated"] is False
    assert item["trade_setup"]["level_model"] == "causal_level_zones_v2+invalidation_first_v2+vrvp"
    assert PRIVATE not in json.dumps(item)


def test_real_cache_clock_keeps_naive_time_ambiguous_and_analysis_clock_separate(reader):
    data = payload()
    data.pop("timestamp")
    data["cached_at"] = "2026-10-08T12:45:59.123456"
    data["diagnostics"]["analysis_as_of"] = STAMP
    data["results"][1].pop("analysis_as_of")
    result = project(reader, data)
    assert result["cache"]["cached_at"] == data["cached_at"]
    assert result["cache"]["cached_at_timezone"] == "unknown_local_time"
    assert "cached_at_utc" not in result["cache"] and "timestamp_utc" not in result["cache"]
    assert result["cache"]["diagnostics"] == {"analysis_as_of": "2026-10-07T20:00:00+00:00"}
    assert projected(result)["scanner_timestamps"] == {}
    assert "run_id" not in json.dumps(result)
    data["cached_at"] = "2026-10-08T14:45:59+02:00"
    result = project(reader, data)
    assert result["cache"]["cached_at_utc"] == "2026-10-08T12:45:59+00:00"
    assert result["cache"]["cached_at_timezone"] == "explicit_utc_offset"
    for value in (PRIVATE, "2026-10-08T12:45:59" + PRIVATE, False):
        data["cached_at"] = value
        assert "cached_at" not in project(reader, data)["cache"]
    data["diagnostics"]["analysis_as_of"] = "2026-10-07T20:00:00"
    assert project(reader, data)["cache"]["diagnostics"] == {}


def test_bad_selected_shape_is_per_candidate_invalid_not_silently_empty(reader):
    for field in ("trade_setup", "native_plan_diagnostics", "level_structure"):
        row = candidate()
        row[field] = [PRIVATE]
        result = project(reader, {"results": [row, candidate("RELL")]})
        assert projected(result)["status"] == "invalid"
        assert projected(result)["reason"] == "candidate_shape_invalid"
        assert projected(result, "RELL")["status"] == "ok"
        assert PRIVATE not in json.dumps(result)


def test_structure_symbol_conflict_does_not_claim_another_tickers_geometry(reader):
    row = candidate()
    row["level_structure"]["symbol"] = "RELL"
    item = projected(project(reader, {"results": [row]}))
    assert item["status"] == "invalid" and item["reason"] == "structure_symbol_conflict"
    assert "level_structure" not in item


def test_invalid_numeric_observations_never_export_or_become_zero(reader):
    for value in (True, False, float("nan"), float("inf"), -float("inf"), 10**100, "1999999.125", PRIVATE):
        row = candidate()
        row["MedianDollarVol20"] = value
        row["trade_setup"]["risk"] = value
        item = projected(project(reader, {"results": [row]}))
        assert "MedianDollarVol20" not in item["scanner_numbers"]
        assert "risk" not in item["trade_setup"]
        assert item["scanner_numbers"]["median_dollar_volume_20d"] == 1_999_999.125
        assert "median_dollar_volume_20d" in item["alias_conflicts"]
        assert PRIVATE not in json.dumps(item, allow_nan=False)


def test_counts_and_boolean_history_are_strict_and_bounded(reader):
    for value in (True, -1, 1.5, 1_000_001, "70", PRIVATE):
        row = candidate()
        row.update(History_Bars=value, History_OK=1)
        row["level_structure"]["completed_bar_counts"]["1D"] = value
        row["level_structure"]["zones"][0]["evidence"][0]["provenance"]["pivot_index"] = value
        item = projected(project(reader, {"results": [row]}))
        assert "History_Bars" not in item["scanner_numbers"]
        assert "History_OK" not in item["scanner_booleans"]
        assert "1D" not in item["level_structure"]["completed_bar_counts"]
        assert "pivot_index" not in item["level_structure"]["zones"][0]["evidence"][0]["provenance"]


def test_secret_injection_into_every_text_protocol_field_remains_private(reader):
    row = candidate()
    row.update(analysis_as_of=PRIVATE, native_plan_reason=PRIVATE, native_plan_status=PRIVATE,
               swing_analysis_session=PRIVATE, price_source=PRIVATE, Strategy=PRIVATE, grade=PRIVATE)
    row["nearest_barrier"].update(source=PRIVATE, timeframe=PRIVATE, zone_id=PRIVATE,
                                independence_key=PRIVATE, action=PRIVATE, confirmed_at=PRIVATE)
    zone = row["level_structure"]["zones"][0]
    zone.update(zone_id=PRIVATE, sources=[PRIVATE], origin_roles=[PRIVATE], quality_flags=[PRIVATE])
    zone["evidence"][0].update(source_family=PRIVATE, source_name=PRIVATE, timeframe=PRIVATE,
                               independence_key=PRIVATE, observed_at=PRIVATE)
    zone["evidence"][0]["provenance"].update(role_hint=PRIVATE, session_closed_at=PRIVATE)
    row["trade_setup"].update(tp1_source=PRIVATE, stop_source=PRIVATE, direction=PRIVATE)
    result = project(reader, {"results": [row]})
    assert projected(result)["status"] == "ok"
    assert PRIVATE not in json.dumps(result, allow_nan=False)


def test_snapshot_metadata_has_an_independent_strict_whitelist(reader):
    result = reader.project_payload(payload(), {"name": PRIVATE, "sha256": PRIVATE,
                                               "size_bytes": True, "mtime_ns": -1, "email": PRIVATE})
    assert result["cache"]["name"] == reader.CACHE_NAME
    assert "sha256" not in result["cache"] and "size_bytes" not in result["cache"]
    assert PRIVATE not in json.dumps(result)


def test_invalid_or_non_final_cache_shapes_fail_closed(reader):
    for shape in ([], {}, {"results": {}}, {"results": [None]}, {"results": [], "partial": True},
                  {"results": [], "partial": "false"}, {"results": [], "partial": None}, {"results": [], "partial": 0}):
        with pytest.raises(reader.EvidenceError, match="invalid_cache_shape"):
            reader.project_payload(shape, {})


def test_empty_final_cache_is_readable_with_four_missing_statuses(reader):
    result = project(reader, {"results": [], "partial": False})
    assert result["status"] == "ok" and result["cache"]["rows_total"] == 0
    assert all(row["status"] == "not_found" for row in result["candidates"])


def test_list_and_total_evidence_bounds_do_not_truncate_or_invent_completeness(reader, monkeypatch):
    with pytest.raises(reader.EvidenceError, match="invalid_cache_shape"):
        project(reader, {"results": [{}] * (reader.MAX_ROWS + 1)})
    for field in ("zones", "evidence", "sources"):
        row = candidate()
        if field == "zones":
            row["level_structure"]["zones"] *= reader.MAX_ZONES + 1
        elif field == "evidence":
            row["level_structure"]["zones"][0]["evidence"] *= reader.MAX_EVIDENCE + 1
        else:
            row["level_structure"]["zones"][0]["sources"] = ["PDH"] * 257
        assert projected(project(reader, {"results": [row]}))["status"] == "invalid"
    monkeypatch.setattr(reader, "MAX_TOTAL_EVIDENCE", 1)
    row = candidate()
    row["level_structure"]["zones"] *= 2
    assert projected(project(reader, {"results": [row]}))["status"] == "invalid"


def test_string_protocol_bounds_and_lookalikes_are_rejected(reader):
    assert reader._timestamp("2026-10-07T20:00:00") is None  # No timezone authority.
    assert reader._timestamp(STAMP + PRIVATE) is None
    assert reader._timeframe("1D/" * 30) is None
    assert reader._timeframe("1D/" + PRIVATE) is None
    assert reader._source_label("confirmed_swing_high (1D) " + PRIVATE) is None
    assert reader._source_label("PDH + " * 100 + "PDH (1D)") is None
    assert reader._independence_key("horizontal_swing:1D:" + PRIVATE) is None
    assert reader._independence_key("session:1D:" + PRIVATE) is None
    assert reader._zone_ids({"zone_id": ZONE + PRIVATE}, ("zone_id",)) == {}


def test_invalid_or_duplicate_json_keys_never_choose_a_winner(reader, tmp_path):
    path = tmp_path / reader.CACHE_NAME
    for raw in ('{"results":[],"results":[]}', '{"results":[{"ticker":"UVE","ticker":"RELL"}]}',
                '{"results":[{"ticker":"UVE","trade_setup":{"tp1":1,"tp1":2}}]}', 'not json'):
        path.write_text(raw, encoding="utf8")
        with pytest.raises(reader.EvidenceError, match="invalid_json"):
            reader.read_cache(path)


def test_leaf_symlink_and_directory_are_not_read_as_cache(reader, tmp_path):
    target = write_cache(tmp_path)
    alias = tmp_path / "alias.json"
    with pytest.raises(reader.EvidenceError, match="not_regular"):
        reader.read_cache(tmp_path)
    try:
        alias.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Host cannot create an unprivileged symlink")
    with pytest.raises(reader.EvidenceError, match="not_regular"):
        reader.read_cache(alias)


def test_oversized_file_rejected_before_open(reader, tmp_path, monkeypatch):
    path = write_cache(tmp_path)
    monkeypatch.setattr(reader, "MAX_BYTES", 32)
    monkeypatch.setattr(reader.os, "open", lambda *args: pytest.fail("Oversized cache must not be opened"))
    with pytest.raises(reader.EvidenceError, match="too_large"):
        reader.read_cache(path)


def test_path_replacement_between_stat_and_open_invalidates_read(reader, tmp_path, monkeypatch):
    path = write_cache(tmp_path)
    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"results":[]}', encoding="utf8")
    original_open = reader.os.open
    def racing_open(name, flags, *args, **kwargs):
        os.replace(replacement, path)
        return original_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(reader.os, "open", racing_open)
    with pytest.raises(reader.EvidenceError, match="changed_during_read"):
        reader.read_cache(path)


def test_post_hash_path_identity_is_checked_even_when_open_inode_is_unchanged(reader, tmp_path, monkeypatch):
    path = write_cache(tmp_path)
    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"results":[]}', encoding="utf8")
    original_lstat = Path.lstat
    calls = []
    def racing_lstat(named, *args, **kwargs):
        if named == path:
            calls.append(named)
            if len(calls) > 1:
                return original_lstat(replacement, *args, **kwargs)
        return original_lstat(named, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", racing_lstat)
    with pytest.raises(reader.EvidenceError, match="changed_during_read"):
        reader.read_cache(path)


def test_open_file_change_during_read_invalidates_hash(reader, tmp_path, monkeypatch):
    path = write_cache(tmp_path)
    original_fstat = reader.os.fstat
    calls = []
    def racing_fstat(descriptor):
        if calls:
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000))
        calls.append(descriptor)
        return original_fstat(descriptor)
    monkeypatch.setattr(reader.os, "fstat", racing_fstat)
    with pytest.raises(reader.EvidenceError, match="changed_during_read"):
        reader.read_cache(path)


def test_service_change_or_recycled_pid_discards_the_snapshot(reader, tmp_path, monkeypatch):
    root = tmp_path / "namespace"
    (root / "tmp").mkdir(parents=True)
    write_cache(root / "tmp")
    monkeypatch.setattr(reader, "process_root", lambda pid: root)
    for identity in ("pid", "start_ticks"):
        pids = iter([71, 72] if identity == "pid" else [71, 71])
        starts = iter([12345, 12346] if identity == "start_ticks" else [12345, 12345])
        monkeypatch.setattr(reader, "service_pid", lambda: next(pids))
        monkeypatch.setattr(reader, "process_start_ticks", lambda pid: next(starts))
        with pytest.raises(reader.EvidenceError, match="service_changed"):
            reader.collect()


def test_collect_fixed_namespace_is_read_only_and_does_not_touch_environment(reader, tmp_path, monkeypatch):
    root = tmp_path / "namespace"
    (root / "tmp").mkdir(parents=True)
    path = write_cache(root / "tmp")
    original = path.read_bytes()
    monkeypatch.setattr(reader, "service_pid", lambda: 71)
    monkeypatch.setattr(reader, "process_start_ticks", lambda pid: 12345)
    monkeypatch.setattr(reader, "process_root", lambda pid: root)
    monkeypatch.setattr(reader.os, "getenv", lambda *args: pytest.fail("No environment reads"))
    result = reader.collect()
    assert result["api"] == {"unit": "tradingbot-api.service", "pid": 71, "start_ticks": 12345}
    assert projected(result)["status"] == "ok" and path.read_bytes() == original


def test_service_pid_is_bounded_and_subprocess_is_a_fixed_read_only_command(reader, monkeypatch):
    def fake_run(command, **kwargs):
        assert command == ["/usr/bin/systemctl", "show", "tradingbot-api.service", "--property=MainPID", "--value"]
        assert kwargs == {"check": True, "capture_output": True, "text": True, "timeout": 10}
        return SimpleNamespace(stdout=stdout)
    monkeypatch.setattr(reader.subprocess, "run", fake_run)
    for stdout in ("0", "-1", "", "71\n72", PRIVATE, "2147483648"):
        with pytest.raises(reader.EvidenceError, match="api_inactive"):
            reader.service_pid()


def test_process_start_reads_bounded_proc_identity_not_environment_or_cmdline(reader, monkeypatch):
    tail = ["S"] + ["0"] * 18 + ["12345"] + ["0"] * 10
    def fake_open(path, mode, encoding):
        assert path == Path("/proc/71/stat") and mode == "r" and encoding == "ascii"
        return io.StringIO("71 (process ) name) " + " ".join(tail))
    monkeypatch.setattr("builtins.open", fake_open)
    assert reader.process_start_ticks(71) == 12345


def test_bad_or_oversized_proc_identity_is_fixed_failure(reader, monkeypatch):
    for text in ("71 bad stat", "71 (api) S 0", "71 (api) " + "0 " * 3000):
        monkeypatch.setattr("builtins.open", lambda *args, **kwargs: io.StringIO(text))
        with pytest.raises(reader.EvidenceError, match="service_identity_unavailable"):
            reader.process_start_ticks(71)


def test_cli_unexpected_error_never_prints_paths_provider_text_or_secrets(reader, monkeypatch, capsys):
    monkeypatch.setattr(reader.sys, "argv", ["-"])
    monkeypatch.setattr(reader, "os", SimpleNamespace(name="posix", O_NOFOLLOW=0))
    def fail():
        raise error
    monkeypatch.setattr(reader, "collect", fail)
    for error in (RuntimeError(PRIVATE), ValueError(PRIVATE), reader.EvidenceError(PRIVATE)):
        assert reader.main() == 1
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["reason"] == "reader_failed"
        assert PRIVATE not in captured.out and captured.err == ""


def test_cli_output_limit_is_checked_before_printing_candidate_data(reader, monkeypatch, capsys):
    monkeypatch.setattr(reader.sys, "argv", ["-"])
    monkeypatch.setattr(reader, "os", SimpleNamespace(name="posix", O_NOFOLLOW=0))
    monkeypatch.setattr(reader, "MAX_OUTPUT_BYTES", 32)
    monkeypatch.setattr(reader, "collect", lambda: project(reader))
    assert reader.main() == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["reason"] == "output_too_large"
    assert "candidates" not in json.loads(captured.out)


def test_stdin_cli_is_standalone_ascii_and_has_no_arbitrary_arguments():
    source = READER_PATH.read_text(encoding="utf8")
    source.encode("ascii")
    completed = subprocess.run([sys.executable, "-I", "-", "unexpected-path-or-ticker"],
                               input=source, text=True, capture_output=True, timeout=10)
    assert completed.returncode == 1 and completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result["kind"] == "momentum_candidate_evidence"
    assert result["read_only"] is True and result["reason"] == "unsupported_arguments"


def test_standalone_source_imports_only_stdlib_and_has_no_network_or_environment_access():
    source = READER_PATH.read_text(encoding="utf8")
    tree = ast.parse(source)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Attribute):
            assert node.attr not in {"environ", "getenv", "putenv", "system", "socket", "connect", "urlopen"}
    assert imports <= sys.stdlib_module_names
    assert imports.isdisjoint({"api", "modules", "requests", "socket", "urllib", "http"})


def test_powershell_wrapper_parses_without_executing_ssh(tmp_path):
    assert WRAPPER_PATH.is_file()
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is not installed on this test host")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    code = """
$tokens = $null
$errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile(WRAPPER_PATH, [ref]$tokens, [ref]$errors)
if (@($errors).Count -ne 0) { exit 1 }
exit 0
""".replace("WRAPPER_PATH", quote(WRAPPER_PATH))
    encoded = base64.b64encode(code.encode("utf-16le")).decode("ascii")
    completed = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                               text=True, capture_output=True, timeout=15)
    assert completed.returncode == 0 and completed.stderr == ""


def test_mocked_ssh_wrapper_validates_reply_identity_types_and_never_overwrites(reader, tmp_path):
    assert WRAPPER_PATH.is_file()
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is not installed on this test host")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    for scenario in ("success", "ssh_failure", "invalid_json", "wrong_ticker", "wrong_status",
                     "wrong_read_only_string", "wrong_schema_string", "oversized_reply", "existing_file"):
        case_root = tmp_path / scenario
        scripts = case_root / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(READER_PATH, scripts / READER_PATH.name)
        shutil.copy2(WRAPPER_PATH, scripts / WRAPPER_PATH.name)
        response = project(reader)
        if scenario == "wrong_ticker":
            response["candidates"][0]["ticker"] = PRIVATE
        elif scenario == "wrong_status":
            response["candidates"][0]["status"] = PRIVATE
        elif scenario == "wrong_read_only_string":
            response["read_only"] = "true"
        elif scenario == "wrong_schema_string":
            response["schema_version"] = "1"
        elif scenario == "oversized_reply":
            response["extra"] = "x" * (2 * 1024 * 1024 + 1)
        reply = case_root / "fixture-reply.json"
        reply.write_text("not json" if scenario == "invalid_json" else json.dumps(response), encoding="utf8")
        export = case_root / "output" / "profitability" / "momentum-candidate-evidence-20261008T123456Z.json"
        if scenario == "existing_file":
            export.parent.mkdir(parents=True)
            export.write_text("preserved", encoding="utf8")
        # No actual SSH invocation: a local PowerShell function consumes the
        # reviewed source but returns only the isolated fixture response.
        code = """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Get-Date { [datetime]::SpecifyKind([datetime]'2026-10-08T12:34:56', [DateTimeKind]::Utc) }
function ssh {
    if (($args -join '|') -cne '-T|-o|StrictHostKeyChecking=yes|-o|ConnectTimeout=10|root@178.104.69.209|/usr/bin/python3 -I -') {
        throw 'Unexpected SSH contract'
    }
    $pipedSource = @($input) -join "`n"
    if (-not $pipedSource.Contains('def collect():') -or -not $pipedSource.Contains('TICKERS = ("RELL", "UVE", "NECB", "GKOS")')) {
        throw 'Standalone collector source not streamed'
    }
    $global:LASTEXITCODE = EXIT_CODE
    Get-Content -Raw -LiteralPath REPLY_PATH
}
try { & WRAPPER_PATH; exit 0 } catch { Write-Output ('fixture_wrapper_failed: ' + $_.Exception.Message); exit 1 }
""".replace("EXIT_CODE", "7" if scenario == "ssh_failure" else "0").replace(
            "REPLY_PATH", quote(reply)).replace("WRAPPER_PATH", quote(scripts / WRAPPER_PATH.name))
        encoded = base64.b64encode(code.encode("utf-16le")).decode("ascii")
        completed = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                    "-EncodedCommand", encoded], text=True, capture_output=True, timeout=20)
        if scenario == "success":
            assert completed.returncode == 0, completed.stdout + completed.stderr
            saved = json.loads(export.read_text(encoding="utf8"))
            assert saved == response and PRIVATE not in export.read_text(encoding="utf8")
            saved_uve = projected(saved)
            assert set(saved_uve["ticker_aliases"]) == {"ticker", "Ticker"}
            assert {"score", "Score", "TP1", "tp1"} <= set(saved_uve["scanner_numbers"])
        else:
            assert completed.returncode != 0, scenario
            if scenario == "existing_file":
                assert export.read_text(encoding="utf8") == "preserved"
            else:
                assert not export.exists(), scenario
