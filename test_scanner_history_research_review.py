"""Independent, offline research-protocol counterexamples and controls."""
import json

import pytest

from scripts import scanner_history_audit as sources
from scripts.scanner_history_stock import summarize_observations


def _source_identity_inventory():
    records = [dict(file=f"stock-{symbol}-1day.json", symbol=symbol,
                    venue="us_equity_polygon", multiplier=1, span="day")
               for symbol in sources.STOCKS + sources.BIOTECH + sources.PENNIES]
    records.extend(dict(file=f"stock-{symbol}-{multiplier}minute.json", symbol=symbol,
                        venue="us_equity_polygon", multiplier=multiplier, span="minute")
                   for symbol in sources.STOCKS for multiplier in (30, 5))
    records.extend(dict(file=f"crypto-{symbol}-5m.json", symbol=symbol,
                        venue="binance_spot", timeframe="5m") for symbol in sources.CRYPTO)
    return [dict(record, status="unavailable", error_code="offline_fixture_no_source") for record in records]


def _write_inventory(directory, records):
    protocol = sources.manifest()
    (directory / "manifest.json").write_text(json.dumps(protocol), encoding="utf-8")
    (directory / "inventory.json").write_text(json.dumps({
        "manifest_sha256": sources.fingerprint(protocol), "sources": records,
        "complete": True, "all_sources_available": True,
    }), encoding="utf-8")


@pytest.mark.parametrize("fault", ["empty", "missing", "duplicate", "wrong_interval"])
def test_frozen_source_coverage_requires_exact_predeclared_18_source_identities(tmp_path, fault):
    records = _source_identity_inventory()
    if fault == "empty":
        records = []
    elif fault == "missing":
        records.pop()
    elif fault == "duplicate":
        records[-1] = dict(records[0])
    else:
        records[0]["multiplier"] = 2
    _write_inventory(tmp_path, records)
    with pytest.raises(ValueError):
        sources.verify_sources(tmp_path)


def test_unavailable_complete_identity_population_is_not_available_or_a_measured_zero(tmp_path):
    records = _source_identity_inventory()
    assert len(records) == 18
    _write_inventory(tmp_path, records)
    report = sources.verify_sources(tmp_path)
    assert len(report["sources"]) == 18
    assert report["all_sources_available"] is False
    assert all(record["status"] == "unavailable" for record in report["sources"])


def test_stock_study_fill_denominator_excludes_open_unfilled_and_ambiguous_winner_claims():
    records = [
        dict(entry_filled=True, evaluation_status="DECIDED", outcome="TP1", r_multiple=1.),
        dict(entry_filled=True, evaluation_status="DECIDED", outcome="STOP", r_multiple=-1.),
        dict(entry_filled=True, evaluation_status="DECIDED", outcome="STOP", r_multiple=-1.,
             r_multiple_upper=2., intrabar_ambiguous=True),
        dict(entry_filled=False, evaluation_status="OPEN_INVALIDATED_PLAN", outcome="NO_FILL", r_multiple=100.),
        dict(entry_filled=True, evaluation_status="INCOMPLETE_HOLDING_WINDOW", outcome="UNRESOLVED", r_multiple=100.),
    ]
    report = summarize_observations(records)
    assert report["modeled_plans"] == 5
    assert report["fills"] == 4
    assert report["no_fill"] == report["unresolved"] == report["ambiguous"] == 1
    assert report["decided_including_ambiguous_bounds"] == 3
    assert report["unambiguous_decided"] == 2
    assert report["unambiguous_wins"] == 1
    assert report["sample_win_rate_pct"] == 50.
    assert report["mean_net_r_unambiguous"] == 0.
    assert report["lower_sum_net_r"] == -1.
    assert report["upper_sum_net_r"] == 2.
    assert report["wilson95_pct"] == pytest.approx([9.453120573423077, 90.54687942657693])


def test_empty_stock_study_is_unknown_not_zero_percent_or_zero_net_profit():
    report = summarize_observations([])
    for key in ("sample_win_rate_pct", "wilson95_pct", "mean_net_r_unambiguous",
                "lower_sum_net_r", "upper_sum_net_r"):
        assert report[key] is None


def test_real_tracker_report_keeps_unknown_origin_win_separate_and_quarantines_future_outcome(tmp_path, monkeypatch):
    from scripts import scanner_history_tracker as old_tracker
    from scripts.signal_performance_breakdown import REPORT_COLUMNS
    from test_signal_performance_breakdown import _row
    monkeypatch.setattr(old_tracker, "ROOT", tmp_path)
    rows = [
        _row(1, status="TP2_HIT", r_realized=2., origin_evidence=None),
        _row(2, origin_evidence="direct_post_send"),
        _row(3, status="TP2_HIT", r_realized=20., closed_at="2026-10-01T00:00:00+00:00"),
    ]
    projected = [dict.fromkeys(REPORT_COLUMNS) | row for row in rows]
    source = tmp_path / "server-snapshot.json"
    source.write_text(json.dumps(dict(
        kind="private_server_evidence", schema_version=1, read_only=True,
        captured_at="2026-09-30T20:00:00Z", tracker=dict(rows=projected, inventory=dict(
            all_rows=3, trade_rows=3, shadow_rows=0, other_rows=0, missing_report_columns=[])),
    )), encoding="utf-8")
    report = old_tracker.report(source, tmp_path / "output" / "report.json")
    assert report["current_scanner_win_rate"] is None
    assert report["full_three_month_coverage"] is False
    assert report["missing_later_period"] is True
    assert report["window_recorded_rows"] == 3
    cell, = report["cells"]
    assert cell["quarantined_future_outcome_rows"] == 1
    assert cell["metrics"]["decided_signals"] == 2
    assert cell["metrics"]["win_rate_pct"] == 50.
    assert cell["qualified_origin_metrics"]["decided_signals"] == 1
    assert cell["qualified_origin_metrics"]["win_rate_pct"] == 0.
    assert cell["qualified_origin_metrics"]["qualified_origin_rows"] == 1


def test_real_bear_history_request_includes_signal_day_without_truncating_60_prior_levels(monkeypatch):
    import api
    from scripts.scanner_history_special_stocks import run_daily_observation_wrappers
    from test_scanner_history_special_stocks import bear_snapshot_fixture
    as_of, history, previous = bear_snapshot_fixture()
    # Only this older, actual prior support provides the second structure
    # target. Today's low94 is first; the most recent20 lows99 are above96.
    history[-40]["l"] = 90.
    _, bears = run_daily_observation_wrappers(api,
        [dict(ticker="AAPL", day=history[-1], prevDay=previous)],
        {"AAPL": history}, as_of=as_of)
    row, = bears
    assert row.get("level_model") in {"bear_structure_first_v2", "bear_structure_first_v2+vrvp"}, row
    assert row["TP1"] == 94.
    assert row["TP2"] == 90.
    assert row["vrvp_native_targets_preserved"] == "closer_observed_bear_levels"
    assert row["nearest_barrier"]["price"] == 94.
    assert row["nearest_barrier"]["causal_structure_validated"] is False
    assert row["barrier_gate_active"] is True
    assert row["entry_eligible"] is False


@pytest.mark.parametrize("payload", [
    {"status": "OK", "results": None},
    {"status": "ERROR", "results": []},
    {"status": "OK", "results": [], "adjusted": None},
    {"status": "OK", "results": [], "adjusted": False},
])
def test_bear_history_invalid_provider_contract_cannot_manufacture_plan_or_baseline(monkeypatch, payload):
    import api
    from test_stock_deep_audit_20261002 import _bear_daily_history_fixture, Response
    _, plans, saved = _bear_daily_history_fixture(monkeypatch)
    provider = api.rate_limited_get
    observed = []
    def invalid_history(url, **kwargs):
        if "/aggs/" in url:
            observed.append((url, kwargs))
            return Response(payload)
        return provider(url, **kwargs)
    monkeypatch.setattr(api, "rate_limited_get", invalid_history)
    api._bear_scan_wrapper()
    assert plans == []
    assert saved[0]["breakdown_stocks"] == []
    assert saved[0]["diagnostics"]["history_fetch_errors"] == 1
    url, request = observed[0]
    assert url.endswith("/2025-09-30/2026-09-30"), url
    assert request["params"]["limit"] == 64
    assert request["params"]["adjusted"] == "true"


@pytest.mark.parametrize("version,expected_invalid", [("v1", True), ("v2", False)])
def test_actual_vrvp_enrichment_preserves_bear_model_contract_in_final_classifier(monkeypatch, version, expected_invalid):
    import api
    from datetime import datetime, timezone
    from modules.vrvp_levels import apply_vrvp_to_trade_setup
    from test_vrvp_trade_levels import _build_causal_daily_structure, _bars_with_nodes
    vrvp = _build_causal_daily_structure(_bars_with_nodes(low_node=88, high_node=105), 100, "SHORT")
    setup = dict(entry=100., stop=103., tp1=96., tp2=94., direction="SHORT",
                 level_model=f"bear_structure_first_{version}")
    enriched = apply_vrvp_to_trade_setup(setup, vrvp, direction="SHORT", asset_type="stock_swing", atr=2.)
    assert enriched["vrvp_applied"] is True
    assert enriched["level_model"] == f"bear_structure_first_{version}+vrvp"
    monkeypatch.setattr(api, "_load_common_stock_universe_cached", lambda **kw: ({"AAPL"}, "offline_fixture"))
    row = dict(ticker="AAPL", grade="S", score=88, rvol=2.1, price=100., change_pct=-6.5,
               open_to_current_pct=-4., close_pos=.18, latest_bar_change_pct=-.2,
               latest_bar_close_pos=.18, **enriched)
    row["trade_setup_source"] = "native_bear_vrvp_structure"
    state = api._classify_alert_candidate("bear", row,
        now=datetime(2026, 10, 1, 22, tzinfo=timezone.utc).timestamp(), cache_only=True)
    assert ("bear_plan_contract_invalid" in state["suppression_reasons"]) is expected_invalid, state


@pytest.mark.parametrize("first_source", ["day_low_liquidity", "20d_low_support", "60d_low_support"])
def test_vrvp_never_skips_current_bear_observed_first_target_to_meet_rr(first_source):
    from modules.vrvp_levels import apply_vrvp_to_trade_setup
    from test_vrvp_trade_levels import _build_causal_daily_structure, _bars_with_nodes
    vrvp = _build_causal_daily_structure(_bars_with_nodes(low_node=88, high_node=105), 100, "SHORT")
    setup = dict(entry=100., stop=103., tp1=99.2, tp2=94., direction="SHORT",
                 level_model="bear_structure_first_v2", tp1_source=first_source,
                 tp2_source="60d_low_support")
    result = apply_vrvp_to_trade_setup(setup, vrvp, direction="SHORT", asset_type="stock_swing", atr=2.)
    assert result["tp1"] == 99.2
    assert result["tp2"] == 94.
    assert result["nearest_barrier"]["price"] == 99.2
    assert result["nearest_barrier"]["causal_structure_validated"] is False
    assert "tp1_zone_id" not in result
    assert result["barrier_gate_active"] is True
    assert result["entry_eligible"] is False


def test_vrvp_bear_target_preservation_cannot_upgrade_unverified_structural_claim():
    from modules.vrvp_levels import apply_vrvp_to_trade_setup
    from test_vrvp_trade_levels import _build_causal_daily_structure, _bars_with_nodes
    vrvp = _build_causal_daily_structure(_bars_with_nodes(low_node=88, high_node=105), 100, "SHORT")
    setup = dict(entry=100., stop=103., tp1=99.2, tp2=94., direction="SHORT",
                 level_model="bear_structure_first_v2", tp1_source="day_low_liquidity",
                 tp2_source="60d_low_support", tp1_is_projection=False)
    result = apply_vrvp_to_trade_setup(setup, vrvp, direction="SHORT", asset_type="stock_swing", atr=2.)
    assert result["structure_status"] == "REJECT"
    assert result["structure_reason"] == "tp1_marked_structural_without_causal_identity"
    assert result["entry_eligible"] is False
    assert "vrvp_native_targets_preserved" not in result
