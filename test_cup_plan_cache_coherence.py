"""A young Cup shape cache is reusable only with a coherent final-plan receipt.

These tests use the real detector, daily/weekly zones, plan/receipt guards,
JSON cache readers, REST decoration and persisted-attempt reader. Only time,
external 4H history and isolated cache/runtime paths are substituted. Run with
scripts/run_offline_tests.py; do not import api outside the offline launcher.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os

import pytest

import api
from test_cup_final_plan_coherence import AS_OF, NOW, SESSION, _causal_cup_inputs


CUP = "Cup and Handle Breakout"


@pytest.fixture
def cache_io(monkeypatch, tmp_path):
    class CacheClock(datetime):
        @classmethod
        def now(cls, tz=None):
            # Legacy cache writers use host-local naive wall time. Preserve
            # that rule so REST age and the UTC attempt interval agree on
            # every host, rather than silently assuming the host is UTC.
            return (NOW.astimezone(tz) if tz is not None else
                    NOW.astimezone().replace(tzinfo=None))

    monkeypatch.setattr(api, "datetime", CacheClock)
    monkeypatch.setattr(api.time, "time", lambda: NOW.timestamp())
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("ALPHA_RUNTIME_TMP_DIR", str(runtime))
    shared = tmp_path / "strategy_scan.json"
    leaves = {name: tmp_path / f"strategy_{index}.json"
              for index, name in enumerate(api._AUTO_STOCK_ALERT_STRATEGIES)}
    assert CUP in leaves
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", {
        **api.SCAN_CACHE_MAP, "strategy_scan": str(shared)})
    monkeypatch.setattr(api, "STRATEGY_SCAN_CACHE", str(shared))
    monkeypatch.setattr(api, "_strategy_cache_path", lambda name, *args: str(leaves[name]))
    monkeypatch.setattr(api, "_scan_status", {
        "strategy_scan": {"running": False, "interval_min": 60}})
    monkeypatch.setattr(api, "_scan_threads", {})
    monkeypatch.setattr(api, "_scan_resume_restarts", {})

    # Supply actual cache-only common-stock evidence; do not bypass the asset
    # guard or mock decoration/scoring/visibility to keep a row in the result.
    reference = tmp_path / "common_stocks.json"
    reference.write_text(json.dumps({
        "cached_at": NOW.timestamp(), "tickers": ["CUPX"],
        "adr_tickers": [], "names": {"CUPX": "Cup Fixture Corporation"},
    }), encoding="utf8")
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(reference))
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {
        "loaded_at": 0, "tickers": None, "source": "not_loaded",
        "adr_tickers": None, "names": None, "names_refresh_attempted_at": 0,
    })
    market = tmp_path / "market_context.json"
    monkeypatch.setattr(api, "MARKET_CONTEXT_CACHE", str(market))
    cached_at = (api.datetime.now() - timedelta(seconds=30)).isoformat()
    api.save_cache_file(str(market), [{
        "regime": "RISK_ON", "trade_mode": "NORMAL", "overall_risk_score": 0,
        "summary": {"regime": "RISK_ON", "trade_mode": "NORMAL",
                    "headline_level": "LOW", "event_level": "LOW"},
        "source": {"market_internals_observed_at": cached_at},
        "warnings": [],
    }], metadata={"cached_at": cached_at})

    def no_live_provider(*args, **kwargs):
        pytest.fail("Cache reuse and REST projection must not fetch a live provider")

    monkeypatch.setattr(api, "rate_limited_get", no_live_provider)
    stamp = NOW.timestamp() - 30
    return {"shared": shared, "leaves": leaves, "runtime": runtime,
            "stamp": stamp, "cached_at": cached_at}


def _write_scan_cache(path, rows, cache_io, *, strategy=None):
    metadata = {
        "cached_at": cache_io["cached_at"],
        "cache_version": api.STOCK_STRATEGY_CACHE_VERSION,
        "diagnostics": {"coverage": "complete", "final_results": len(rows)},
    }
    if strategy is not None:
        metadata["strategy"] = strategy
        metadata["diagnostics"]["strategy"] = strategy
    api.save_cache_file(str(path), rows, metadata=metadata)
    os.utime(path, (cache_io["stamp"], cache_io["stamp"]))
    # The test relies on real readers seeing a valid, completed current generic
    # envelope. An old cache version must not be the reason for rejection.
    payload = json.loads(path.read_text(encoding="utf8"))
    assert payload["results"] == rows
    assert payload["cache_version"] == api.STOCK_STRATEGY_CACHE_VERSION
    assert payload["diagnostics"]["coverage"] == "complete"


def _populate_startup_caches(cache_io, *, shared_rows=(), cup_rows=()):
    _write_scan_cache(cache_io["shared"], list(shared_rows), cache_io)
    for strategy, path in cache_io["leaves"].items():
        _write_scan_cache(path, list(cup_rows) if strategy == CUP else [],
                          cache_io, strategy=strategy)


def _real_cup_row(monkeypatch, *, far_targets=True):
    candidate, snapshot = _causal_cup_inputs(
        monkeypatch, with_overhead=far_targets, far_targets=far_targets)
    assert snapshot.as_of == AS_OF
    candidate.update(api.stock_swing.metadata(SESSION, 101.7))
    row = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=snapshot)
    assert row is not None
    assert api._cup_signal_contract_valid(row, strategy_name=CUP)
    assert api._cup_final_plan_contract_reason(row) is None
    assert (row["Entry"], row["StopLoss"]) == (101.2, 92.38)
    if far_targets:
        # Independent 118/135 daily highs precede the detector's 180-day
        # window; real native zones give conservative targets, not Cup's
        # separately recorded measured 114.75/128.30 projections.
        assert (row["TP1"], row["TP2"]) == (117.75, 134.75)
        assert row["structure_status"] == "ACCEPT"
        assert api._alert_trade_plan_rejection_reason(row) is None
    else:
        assert row["target_quality"].startswith("PROJECTION_ONLY")
        assert api._alert_trade_plan_rejection_reason(row) == "trade_target_not_structural"
    return row


def _legacy_shape_only(row):
    legacy = deepcopy(row)
    legacy.pop("cup_plan_version")
    legacy["trade_setup"].pop("cup_plan_version")
    # This is precisely the contract gap: shape-v2 itself still passes.
    assert api._cup_signal_contract_valid(legacy, strategy_name=CUP)
    assert api._cup_final_plan_contract_reason(legacy) is not None
    assert api._alert_trade_plan_rejection_reason(legacy) == "trade_cup_final_plan_not_confirmed"
    return legacy


@pytest.mark.parametrize("location", ["shared", "cup_leaf"])
def test_shape_v2_cup_without_final_receipt_cannot_defer_startup(
    cache_io, monkeypatch, location,
):
    row = _legacy_shape_only(_real_cup_row(monkeypatch))
    _populate_startup_caches(
        cache_io, shared_rows=[row] if location == "shared" else [],
        cup_rows=[row] if location == "cup_leaf" else [])
    paths = [cache_io["shared"], *cache_io["leaves"].values()]
    before = {path: path.read_bytes() for path in paths}
    assert api._startup_scan_cache_time("strategy_scan", NOW.timestamp()) is None
    assert {path: path.read_bytes() for path in paths} == before
    assert all(path.stat().st_mtime == cache_io["stamp"] for path in paths)


def test_genuine_current_cup_receipt_preserves_fresh_startup_mtime(cache_io, monkeypatch):
    row = _real_cup_row(monkeypatch)
    _populate_startup_caches(cache_io, shared_rows=[row], cup_rows=[row])
    assert api._startup_scan_cache_time("strategy_scan", NOW.timestamp()) == cache_io["stamp"]


def test_completed_empty_cup_cache_is_not_invented_stale_at_startup(cache_io):
    _populate_startup_caches(cache_io)
    assert api._startup_scan_cache_time("strategy_scan", NOW.timestamp()) == cache_io["stamp"]


def _old_manual_failure_and_later_complete_attempt(cache_io):
    # Bind the actual legacy local cache timestamp to UTC exactly as the
    # production reader does. The result is within a later genuine complete
    # attempt, so invalid Cup receipts are the only missing recovery proof.
    cache_time = datetime.fromisoformat(cache_io["cached_at"]).astimezone(timezone.utc)
    state = {
        "running": False, "last_run_id": "old-manual-owner",
        "last_attempt_at": (cache_time - timedelta(minutes=3)).isoformat(),
        "last_run": (cache_time - timedelta(minutes=10)).isoformat(),
        "last_error": "scan_timeout",
        "last_attempt_diagnostics": {"coverage": "incomplete", "checked": 10},
    }
    key = api._strategy_scan_status_key(CUP, "stocks")
    api._scan_status[key] = deepcopy(state)
    attempt = {
        "schema_version": 1, "attempt_kind": "stock_strategy",
        "strategy_slug": "cup_and_handle_breakout", "run_id": "a" * 32,
        "code_revision": "123456abcdef", "status": "complete",
        "started_at": (cache_time - timedelta(minutes=1)).isoformat(),
        "updated_at": (cache_time + timedelta(seconds=10)).isoformat(),
        "results": [], "result_count": 1, "error_code": None,
        "diagnostics": {"coverage": "complete", "final_results": 1},
    }
    path = cache_io["runtime"] / "stock_strategy_cup_and_handle_breakout_attempt.json"
    path.write_text(json.dumps(attempt), encoding="utf8")
    read = api._read_stock_strategy_attempt(CUP)
    assert read["available"] is True and read["status"] == "complete"
    assert api._stock_attempt_datetime(read["started_at"]) <= cache_time <= api._stock_attempt_datetime(read["updated_at"])
    return key, state, path


def test_rest_shape_only_cup_is_stale_filtered_and_not_complete_cache_recovery(
    cache_io, monkeypatch,
):
    legacy = _legacy_shape_only(_real_cup_row(monkeypatch))
    cache_path = cache_io["leaves"][CUP]
    _write_scan_cache(cache_path, [legacy], cache_io, strategy=CUP)
    key, state, attempt_path = _old_manual_failure_and_later_complete_attempt(cache_io)
    before = cache_path.read_bytes(), attempt_path.read_bytes()
    result = api.get_scan_results(CUP, None, "stocks")
    assert result.count == 0 and result.data == []
    assert result.data_quality["cache_status"] == "stale"
    assert result.data_quality["cache_stale_reason"] == "cup_final_plan_unverified"
    # A current timestamp and later successful attempt cannot acknowledge
    # completion/recovery of a cache whose Cup plan was never finalized.
    assert result.scan_error == "scan_timeout"
    assert result.diagnostics["attempt_diagnostics"]["coverage"] == "incomplete"
    assert result.diagnostics["latest_attempt"]["status"] == "complete"
    assert result.scan_last_completed_at == state["last_run"]
    assert api._scan_status[key] == state
    assert (cache_path.read_bytes(), attempt_path.read_bytes()) == before


def test_rest_genuine_cup_receipt_survives_real_cache_and_decoration(cache_io, monkeypatch):
    row = _real_cup_row(monkeypatch)
    _write_scan_cache(cache_io["leaves"][CUP], [row], cache_io, strategy=CUP)
    key, state, _ = _old_manual_failure_and_later_complete_attempt(cache_io)
    result = api.get_scan_results(CUP, None, "stocks")
    assert result.count == 1
    delivered = result.data[0]
    assert api._cup_final_plan_contract_reason(delivered) is None
    assert (delivered["Entry"], delivered["StopLoss"], delivered["TP1"], delivered["TP2"]) == (
        101.2, 92.38, 117.75, 134.75)
    assert result.data_quality["cache_status"] == "fresh"
    assert result.data_quality.get("cache_stale_reason") != "cup_final_plan_unverified"
    assert result.scan_error is None  # positive control for the recovery test
    assert api._scan_status[key] == state  # response projection, not RAM mutation


def test_rest_current_projection_receipt_remains_visible_as_warning(cache_io, monkeypatch):
    row = _real_cup_row(monkeypatch, far_targets=False)
    _write_scan_cache(cache_io["leaves"][CUP], [row], cache_io, strategy=CUP)
    result = api.get_scan_results(CUP, None, "stocks")
    assert result.count == 1, "A coherent negative plan is not a corrupt legacy cache"
    delivered = result.data[0]
    assert api._cup_final_plan_contract_reason(delivered) is None
    assert delivered["visibility_status"] == "candidate_warning"
    assert delivered["visibility_is_trade_signal"] is False
    assert api._alert_trade_plan_rejection_reason(delivered) == "trade_target_not_structural"
    assert delivered["target_quality"].startswith("PROJECTION_ONLY")
    assert result.data_quality["cache_status"] == "fresh"
    assert result.data_quality.get("cache_stale_reason") != "cup_final_plan_unverified"


def test_rest_completed_empty_cup_cache_is_fresh_without_a_row_receipt(cache_io):
    _write_scan_cache(cache_io["leaves"][CUP], [], cache_io, strategy=CUP)
    result = api.get_scan_results(CUP, None, "stocks")
    assert result.count == 0 and result.data == []
    assert result.data_quality["cache_status"] == "fresh"
    assert result.data_quality.get("cache_stale_reason") != "cup_final_plan_unverified"
    assert result.diagnostics["coverage"] == "complete"


def test_reminder_cannot_take_levels_from_a_shape_only_cup_cache(cache_io, monkeypatch):
    legacy = _legacy_shape_only(_real_cup_row(monkeypatch))
    cache_path = cache_io["leaves"][CUP]
    _write_scan_cache(cache_path, [legacy], cache_io, strategy=CUP)
    before = cache_path.read_bytes()
    with pytest.raises(ValueError, match="^server_scanner_pattern_contract_invalid$"):
        api._structure_reminder_server_row("CUPX", CUP, "LONG")
    assert cache_path.read_bytes() == before


@pytest.mark.parametrize("far_targets", [True, False])
def test_current_cup_cache_is_a_reminder_context_not_automatic_release(
    cache_io, monkeypatch, far_targets,
):
    row = _real_cup_row(monkeypatch, far_targets=far_targets)
    _write_scan_cache(cache_io["leaves"][CUP], [row], cache_io, strategy=CUP)
    context = api._structure_reminder_server_row("CUPX", CUP, "LONG")
    assert api._cup_final_plan_contract_reason(context) is None
    assert (context["Entry"], context["StopLoss"], context["TP1"], context["TP2"]) == (
        row["Entry"], row["StopLoss"], row["TP1"], row["TP2"])
    if not far_targets:
        assert context["structure_status"] == "REJECT"
        assert api._alert_trade_plan_rejection_reason(context) == "trade_target_not_structural"
