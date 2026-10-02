"""September 30 production regressions; no providers, scans, or SMTP."""
import json
import os
from datetime import timedelta

import pytest

import api
from modules import scanners
from test_api_scan_schedule import scheduler, utc
from test_context_scanner_repair import Response


@pytest.mark.parametrize("status", ["OK", "DELAYED"])
def test_biotech_documented_empty_aggregates_are_not_a_provider_failure(monkeypatch, status):
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Response({
        "status": status, "resultsCount": 0, "queryCount": 0,
    }))
    result = scanners._biotech_technical_score("offline", "SVA")
    assert result["data_status"] == "no_history"
    assert result["details"] == {} and result["technical_score"] == 0


@pytest.mark.parametrize("payload", [
    {}, {"status": "OK"}, {"status": "ERROR", "resultsCount": 0},
    {"status": "OK", "resultsCount": 0, "queryCount": 1},
    {"status": "OK", "resultsCount": 0, "results": None},
    {"status": "OK", "resultsCount": 0, "next_url": "PRIVATE"},
])
def test_biotech_missing_or_contradictory_envelope_still_blocks(monkeypatch, payload):
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Response(payload))
    with pytest.raises(scanners.ScannerDataError) as caught:
        scanners._biotech_technical_score("offline", "SVA")
    assert caught.value.code == "scan_data_invalid"
    assert "PRIVATE" not in str(caught.value.diagnostics)


@pytest.mark.parametrize("retained_sibling", [False, True])
def test_biotech_full_scan_excludes_empty_symbol_without_losing_siblings(monkeypatch, retained_sibling):
    monkeypatch.setattr(scanners, "_biotech_clear_stop", lambda: None)
    monkeypatch.setattr(scanners, "_biotech_should_stop", lambda: False)
    monkeypatch.setattr(scanners, "_biotech_universe_cache_load", lambda **kw: [
        {"ticker": "SVA", "name": "Biotechnology"},
        {"ticker": "OTHER", "name": "Other Biotechnology"},
    ])
    monkeypatch.setattr(scanners, "get_premium_catalyst_tickers", lambda **kw: set())
    seen, saved, progress = [], [], []
    def news(key, ticker, **kw):
        seen.append(ticker)
        return {"catalyst_score": 30 if ticker == "SVA" or retained_sibling else 0,
                "catalysts": [], "news": [], "negative_flags": []}
    monkeypatch.setattr(scanners, "_scan_biotech_news", news)
    monkeypatch.setattr(scanners, "get_ticker_details", lambda *a: {"sic_code": "2836"})
    monkeypatch.setattr(scanners, "_get_bpiq_catalysts", lambda *a: {"bpiq_available": False})
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Response({
        "status": "OK", "resultsCount": 0, "queryCount": 0,
    }))
    real_technical = scanners._biotech_technical_score
    def technical(key, ticker, *, as_of=None):
        if ticker == "OTHER":
            return {"technical_score": 20, "details": {"price": 20., "chart_health": 10}}
        return real_technical(key, ticker, as_of=as_of)
    monkeypatch.setattr(scanners, "_biotech_technical_score", technical)
    scored = []
    def risk(*a, **kw):
        assert kw["price"] == 20., "No-history symbol must not enter plan/scoring"
        scored.append(kw["price"])
        return {"risk_score": 0}
    monkeypatch.setattr(scanners, "_biotech_risk_score", risk)
    monkeypatch.setattr(scanners, "_calculate_biotech_catalyst_score", lambda **kw: 85)
    monkeypatch.setattr(scanners, "_calculate_biotech_catalyst_edge", lambda **kw: {})
    monkeypatch.setattr(scanners, "_biotech_cache_save", lambda rows, **kw: saved.append((rows, kw)))
    monkeypatch.setattr(scanners, "_biotech_progress_write", lambda state, **kw: progress.append((state, kw)))
    scanners._biotech_background_scan("offline")
    assert seen == ["SVA", "OTHER"]
    finals = [(rows, meta) for rows, meta in saved if not meta.get("partial")]
    assert len(finals) == 1
    rows, meta = finals[0]
    assert [row["Ticker"] for row in rows] == (["OTHER"] if retained_sibling else [])
    assert scored == ([20.] if retained_sibling else [])
    assert meta["diagnostics"]["no_history"] == 1
    if retained_sibling:
        assert rows[0]["Score"] == 85 and rows[0]["Preis"] == 20.
    assert progress[-1][0] == "done"


@pytest.mark.parametrize("payload", [
    {"results": []},
    {"results": [], "cache_version": 1},
    {"results": [], "cache_version": 17},
    {"results": [], "cache_version": api.STOCK_STRATEGY_CACHE_VERSION, "partial": True},
    {"results": [], "cache_version": api.STOCK_STRATEGY_CACHE_VERSION,
     "diagnostics": {"coverage": "incomplete"}},
])
def test_young_unusable_strategy_cache_does_not_suppress_startup(scheduler, monkeypatch, payload):
    scheduler["now"] = utc("2026-09-30T12:24:00Z")
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: name != "strategy_scan")
    path = api.Path(api.SCAN_CACHE_MAP["strategy_scan"])
    path.write_text(json.dumps(payload), encoding="utf8")
    stamp = scheduler["now"].timestamp() - 30
    os.utime(path, (stamp, stamp))
    api._scheduler_loop()
    assert [name for name, _, _ in scheduler["calls"]] == ["strategy_scan"]
    assert path.stat().st_mtime == stamp  # No fabricated completion or cache rewrite.


@pytest.mark.parametrize("bad_leaf", [False, "version", "old", "future"])
def test_startup_checks_leaf_versions_even_after_recent_manual_scan(scheduler, monkeypatch, tmp_path, bad_leaf):
    scheduler["now"] = utc("2026-09-30T12:24:00Z")
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: name != "strategy_scan")
    monkeypatch.setattr(api, "_strategy_cache_path", lambda name: str(tmp_path / (name + ".json")))
    payload = {"results": [], "cache_version": api.STOCK_STRATEGY_CACHE_VERSION}
    path = api.Path(api.SCAN_CACHE_MAP["strategy_scan"])
    path.write_text(json.dumps(payload))
    stamp = scheduler["now"].timestamp() - 30
    os.utime(path, (stamp, stamp))
    for index, strategy in enumerate(api._AUTO_STOCK_ALERT_STRATEGIES):
        leaf = dict(payload)
        if bad_leaf == "version" and index == 1:
            leaf["cache_version"] -= 1
        leaf_path = api.Path(api._strategy_cache_path(strategy))
        leaf_path.write_text(json.dumps(leaf))
        leaf_stamp = stamp
        if index == 1:
            leaf_stamp += -4000 if bad_leaf == "old" else 4000 if bad_leaf == "future" else 0
        os.utime(leaf_path, (leaf_stamp, leaf_stamp))
    api._scheduler_loop()
    assert [name for name, _, _ in scheduler["calls"]] == (["strategy_scan"] if bad_leaf else [])


def test_busy_startup_heavy_worker_does_not_block_recurring_monitors(scheduler, monkeypatch):
    scheduler["now"] = utc("2026-09-30T12:24:00Z")
    scheduler["timeline"] = [scheduler["now"] + timedelta(minutes=1)]
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: name not in {"biotech", "penny_positions"})
    monkeypatch.setattr(api, "_effective_scan_interval_min", lambda name: 1)
    dispatch, sleep = api._run_scan_safe, api.time.sleep
    def launch(name, fn, **kw):
        if name == "biotech":
            api._scan_status[name]["running"] = True
        return dispatch(name, fn, **kw)
    def nonblocking_sleep(seconds):
        assert seconds != 10, "Scheduler must not wait inside the Biotech worker"
        sleep(seconds)
    monkeypatch.setattr(api, "_run_scan_safe", launch)
    monkeypatch.setattr(api.time, "sleep", nonblocking_sleep)
    api._scheduler_loop()
    assert [name for name, _, _ in scheduler["calls"]].count("penny_positions") == 2
    assert [name for name, _, _ in scheduler["calls"]].count("biotech") == 1
    assert any(name == "biotech" for name, _ in scheduler["watchdogs"])


def test_long_hourly_round_cannot_starve_initial_bi_and_biotech(scheduler, monkeypatch):
    scheduler["now"] = utc("2026-09-30T12:24:00Z")
    scheduler["timeline"] = [scheduler["now"] + timedelta(minutes=step) for step in (61, 122, 183)]
    heavy = {"strategy_scan", "bi_long", "bi_short", "biotech"}
    monkeypatch.setattr(api, "_api_scheduler_should_skip", lambda name: name not in heavy)
    monkeypatch.setattr(api, "_effective_scan_interval_min", lambda name: 60)
    accepted, owner = [], []
    sleep = api.time.sleep
    def launch(name, fn, **kw):
        if owner:
            return False
        owner.append(name)
        accepted.append(name)
        api._scan_status[name]["running"] = True
        return True
    def finish_on_tick(seconds):
        assert seconds != 10, "No synchronous startup wait"
        if seconds == 30 and owner:
            api._scan_status[owner.pop()]["running"] = False
        sleep(seconds)
    monkeypatch.setattr(api, "_run_scan_safe", launch)
    monkeypatch.setattr(api.time, "sleep", finish_on_tick)
    api._scheduler_loop()
    assert accepted == ["strategy_scan", "bi_long", "bi_short", "biotech"]
