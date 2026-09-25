"""Dedicated owner pause/restart, fanout drain and publication fences, offline."""
import ast
from datetime import datetime, timezone
import inspect
import threading
from types import SimpleNamespace

import pytest

import api
from modules import scan_control as control, scan_control_policy as policy
from modules import scanners
from test_scan_control_api import isolated_api, _add_state, _request, _wait_state, OLD_SUCCESS


REAL_TOKEN = api._scan_control_data_token
DEDICATED = sorted(policy.DEDICATED_SCANNERS | {"crypto_strat_long"})


@pytest.mark.parametrize("key", DEDICATED)
def test_restart_fresh_does_not_depend_on_clock_resolution(monkeypatch, key):
    monkeypatch.setattr(api.time, "monotonic", lambda: 123.)
    assert REAL_TOKEN(key) != REAL_TOKEN(key)


@pytest.mark.parametrize("key", DEDICATED)
def test_dedicated_resume_discards_old_work_and_restarts_after_owner_exit(isolated_api, monkeypatch, key):
    context = isolated_api
    monkeypatch.setattr(api, "_scan_control_data_token", REAL_TOKEN)
    entered, boundary, fresh_done = [context.event() for _ in range(3)]
    starts, publications = [], []

    def work():
        starts.append(api._scan_status[key]["last_run_id"])
        if len(starts) == 1:
            entered.set()
            assert boundary.wait(3)
        api._scan_control_point()
        api._scan_control_point(finishing=True)
        publications.append(starts[-1])
        fresh_done.set()

    _add_state(key)
    assert api._run_scan_safe(key, work)
    assert entered.wait(3)
    old_worker = api._scan_threads[key]
    old_id = api._scan_status[key]["last_run_id"]
    _request(key, old_id, "pause")
    boundary.set()
    _wait_state(key, "paused")
    public = api._scan_control_snapshot(key)
    assert public["supported"] and public["resume_policy"] == "restart_fresh"
    assert public["unsupported_reason"] is None and not public["protected"]
    assert not publications and api._scan_status[key]["last_run"] == OLD_SUCCESS
    assert not api._run_scan_safe(key, work)
    _request(key, old_id, "resume")
    old_worker.join(3)
    assert not old_worker.is_alive() and not publications
    assert key in api._scan_resume_restarts
    assert not api._scan_status[key].get("last_error")
    assert api._scan_status[key]["last_run"] == OLD_SUCCESS
    api._drain_scan_resume_restarts()
    assert fresh_done.wait(3)
    context.workers[-1].join(3)
    assert len(starts) == 2 and starts[0] != starts[1]
    assert publications == [starts[1]]
    assert api._scan_control_snapshot(key)["state"] == "finished"
    assert context.cache_checks == [key]


@pytest.mark.parametrize("key", DEDICATED)
def test_dedicated_controls_keep_admin_and_exact_live_run_fence(isolated_api, key):
    _add_state(key)
    with pytest.raises(api.HTTPException) as denied:
        _request(key, "some-run", "pause", authorization="Bearer offline-member")
    assert denied.value.status_code == 403
    with pytest.raises(api.HTTPException) as stale:
        _request(key, "some-run", "pause")
    assert stale.value.status_code == 409
    assert not control.snapshot(key)


@pytest.mark.parametrize("key", ["crypto_explosion", "crypto_strat_long", "early_movers", "btc_divergenz"])
def test_crypto_auto_resume_due_does_not_roll_weekend_to_monday(key, monkeypatch):
    due = datetime(2026, 9, 26, 12, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(api, "_effective_scan_interval_min", lambda name: 5)
    state = {"next_run": datetime.fromtimestamp(due).isoformat()}
    assert api._scan_resume_at(key, state, now=due - 60) == due
    assert api._scan_resume_at("orb", state, now=due - 60) > due


@pytest.mark.parametrize("cancel_before_join", [False, True])
def test_crypto_owner_drains_every_provider_future_before_pause_and_never_publishes_drained_work(
        isolated_api, monkeypatch, cancel_before_join):
    context = isolated_api
    monkeypatch.setattr(api, "_scan_control_data_token", REAL_TOKEN)
    monkeypatch.setattr(api, "_CE_PROGRESS", {})
    monkeypatch.setattr(api, "_CE_RUN_LOCK", threading.Lock())
    entered = threading.Barrier(5)
    release_fast, release_last, drain_seen = [context.event() for _ in range(3)]
    first_run = [True]
    calls, published = [], []
    venues = ("bybit", "binance", "mexc", "bitget")
    universe = [{"exchange": venue, "contract": f"{venue}{i}"} for venue in venues for i in range(2)]
    monkeypatch.setattr(api, "_fetch_crypto_explosion_universe", lambda: (universe, {}))
    original_pending = control.pause_pending
    def pending(owner):
        answer = original_pending(owner)
        if answer:
            drain_seen.set()
        return answer
    monkeypatch.setattr(control, "pause_pending", pending)
    def fetch(contract, venue, **kwargs):
        calls.append((contract, kwargs["timeframe"]))
        if first_run[0] and contract.endswith("0") and kwargs["timeframe"] == "5m":
            entered.wait(3)
            assert (release_last if venue == "bitget" else release_fast).wait(3)
        return []
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", fetch)
    monkeypatch.setattr(api, "_refresh_crypto_funding", lambda row: row)
    monkeypatch.setattr(api, "_score_crypto_explosion_candidate", lambda row,*args: {
        **row, "trade_signal":"EXPLOSION_ARMED", "execution_candle_timestamp":1})
    monkeypatch.setattr(api, "_crypto_candle_freshness", lambda *args: {"fresh":True,"age_seconds":0})
    monkeypatch.setattr(api, "save_cache_file", lambda path,rows,**kwargs: published.append(rows))
    _add_state("crypto_explosion")
    assert api._run_scan_safe("crypto_explosion", api._crypto_explosion_wrapper)
    entered.wait(3)
    old_worker = api._scan_threads["crypto_explosion"]
    old_id = api._scan_status["crypto_explosion"]["last_run_id"]
    _request("crypto_explosion", old_id, "pause")
    release_fast.set()
    assert drain_seen.wait(3)
    assert control.snapshot("crypto_explosion")["state"] == "pause_requested"
    assert not published  # Last venue still has a provider job in flight.
    if cancel_before_join:
        _request("crypto_explosion", old_id, "resume")
    release_last.set()
    if not cancel_before_join:
        _wait_state("crypto_explosion", "paused")
        assert len(calls) == 12  # Four complete candidates; no next candidate.
        for protected in ("new_listing", "penny_positions"):
            _add_state(protected)
            done = context.event()
            assert api._run_scan_safe(protected, done.set)
            assert done.wait(3)
            context.workers[-1].join(3)
        assert not published
        _request("crypto_explosion", old_id, "resume")
    old_worker.join(3)
    assert not old_worker.is_alive() and not published
    assert not api._CE_RUN_LOCK.locked()
    assert api._scan_control_snapshot("crypto_explosion")["state"] == "restart_required"
    first_run[0] = False
    api._drain_scan_resume_restarts()
    fresh_worker = context.workers[-1]
    fresh_worker.join(3)
    assert not fresh_worker.is_alive()
    assert len(published) == 1 and len(published[0]) == 8
    assert api._scan_control_snapshot("crypto_explosion")["state"] == "finished"


@pytest.mark.parametrize("active,requested", [
    ("crypto_trade_signals","crypto_explosion"), ("crypto_trade_signals","new_listing"),
    ("crypto_explosion","crypto_trade_signals"), ("new_listing","crypto_trade_signals"),
])
def test_shared_source_ownership_checks_actual_thread_tail(isolated_api, active, requested):
    _add_state(active)
    _add_state(requested)
    class Alive:
        def is_alive(self):
            return True
    api._scan_threads[active] = Alive()
    assert not api._run_scan_safe(requested, lambda: pytest.fail("overlapping source owner"))


def test_combined_protected_owner_does_not_create_child_pause_owner(isolated_api, monkeypatch):
    owners = []
    def source():
        owners.append(control.bound_owner())
        api._scan_control_point(finishing=True)
    monkeypatch.setattr(api,"_crypto_explosion_wrapper",source)
    monkeypatch.setattr(api,"_new_listing_wrapper",source)
    monkeypatch.setattr(api,"HAS_NEW_LISTING_SCANNER",True)
    monkeypatch.setattr(api,"_build_crypto_trade_signals_from_caches",lambda: ([],{},None,None,[]))
    monkeypatch.setattr(api,"save_cache_file",lambda *args,**kwargs: None)
    _add_state("crypto_trade_signals")
    assert api._run_scan_safe("crypto_trade_signals",api._crypto_trade_signals_wrapper)
    isolated_api.workers[-1].join(3)
    assert owners == [None,None]
    public = api._scan_control_snapshot("crypto_trade_signals")
    assert not public["supported"] and public["protected"]
    assert public["unsupported_reason"] == "mixed_position_management"
    assert not control.snapshot("crypto_explosion")


@pytest.mark.parametrize("name,cache", [
    ("_crypto_strategy_scan_wrapper","_strat_cache"), ("_turtle_scan_wrapper","TURTLE_CACHE"),
    ("_bear_scan_wrapper","BEAR_CACHE"), ("_early_movers_wrapper","EARLY_MOVERS_CACHE"),
    ("_crypto_explosion_wrapper","CRYPTO_EXPLOSION_CACHE"), ("_btc_divergenz_wrapper","BTC_DIVERGENZ_CACHE"),
    ("_money_flow_wrapper","MONEY_FLOW_CACHE"), ("_penny_stock_scanner_wrapper","PENNY_STOCKS_CACHE"),
    ("_volume_spikes_wrapper","VOLUME_SPIKES_CACHE"), ("_orb_scanner_wrapper","ORB_CACHE"),
])
def test_each_final_cache_publication_has_adjacent_owner_seal(name, cache):
    tree = ast.parse(inspect.getsource(getattr(api,name)))
    matched = []
    for parent in ast.walk(tree):
        for _, value in ast.iter_fields(parent):
            if not isinstance(value,list):
                continue
            for index,node in enumerate(value):
                if (isinstance(node,ast.Expr) and isinstance(node.value,ast.Call)
                        and isinstance(node.value.func,ast.Name)
                        and node.value.func.id in {"save_cache_file","finalize_cache_file"}
                        and node.value.args and isinstance(node.value.args[0],ast.Name)
                        and node.value.args[0].id == cache):
                    assert index > 0
                    assert ast.unparse(value[index-1]) == "_scan_control_point(finishing=True)"
                    matched.append(node.lineno)
    assert matched


def test_biotech_loop_and_its_internal_final_cache_are_guarded():
    source = inspect.getsource(scanners._biotech_background_scan)
    assert "for stock in universe:\n            scan_control.safe_point()" in source
    assert "scan_control.seal()\n        _biotech_cache_save(results)" in source


def test_actual_biotech_internal_loop_parks_then_discards_without_final_or_mail(isolated_api, monkeypatch):
    monkeypatch.setattr(api,"_scan_control_data_token",REAL_TOKEN)
    progress, publications, cleanup = [], [], []
    def universe(**kwargs):
        owner = control.bound_owner()
        assert owner[0] == "biotech"
        control.request_pause(*owner,auto_resume=False)
        return [{"ticker":"OFFLINE"}]
    monkeypatch.setattr(scanners,"_biotech_universe_cache_load",universe)
    monkeypatch.setattr(scanners,"_biotech_clear_stop",lambda: None)
    monkeypatch.setattr(scanners,"get_premium_catalyst_tickers",lambda **kwargs: set())
    monkeypatch.setattr(scanners,"_biotech_progress_write",lambda status,**kwargs: progress.append(status))
    monkeypatch.setattr(scanners,"_biotech_cache_save",lambda *args,**kwargs: publications.append(args))
    monkeypatch.setattr(api,"_remove_partial_cache",lambda path: cleanup.append(path))
    monkeypatch.setattr(api,"_check_and_alert",lambda *args: pytest.fail("mail after interrupted biotech"))
    _add_state("biotech")
    assert api._run_scan_safe("biotech",api._biotech_scan_wrapper)
    _wait_state("biotech","paused")
    worker = isolated_api.workers[-1]
    run_id = api._scan_status["biotech"]["last_run_id"]
    assert not publications
    assert progress and all(status == "running" for status in progress)
    _request("biotech",run_id,"resume")
    worker.join(3)
    assert not worker.is_alive() and not publications
    assert cleanup == [api.BIOTECH_CACHE,api.BIOTECH_CACHE]
    assert api._scan_status["biotech"]["last_run"] == OLD_SUCCESS
    assert not api._scan_status["biotech"].get("last_error")


@pytest.mark.parametrize("accepted",[False,True])
@pytest.mark.parametrize("strategy,key",[
    ("Biotech","biotech"),("Early Movers","early_movers"),("Volume Spikes","volume_spikes"),
    ("Penny","penny_stocks"),("Bear","bear"),("Crash","crash_monitor"),
    ("BTC Divergenz","btc_divergenz"),("Money Flow","money_flow"),("Turtle","turtle"),
    ("New Listing","new_listing"),
])
def test_dedicated_alias_ack_reports_actual_admission_and_owner_run(monkeypatch,strategy,key,accepted):
    monkeypatch.setattr(api,"POLYGON_KEY","offline")
    monkeypatch.setattr(api,"HAS_NEW_LISTING_SCANNER",True)
    monkeypatch.setattr(api,"resolve_strategy_name",lambda name,market: name)
    monkeypatch.setattr(api,"get_strategies_for_market",lambda market: {strategy:{}})
    monkeypatch.setattr(api,"_scan_status",{key:{"last_run_id":"actual-owner"}})
    monkeypatch.setattr(api,"_scan_threads",{key:SimpleNamespace(is_alive=lambda:True)})
    observed = []
    monkeypatch.setattr(api,"_run_scan_safe",lambda owner,func: observed.append(owner) or accepted)
    result = api.run_scan(api.ScanRequest(strategy=strategy,market_type="stocks"),None)
    assert observed == [key]
    assert result["accepted"] is accepted
    assert result["status"] == ("started" if accepted else "already_running")
    assert result["run_id"] == "actual-owner"
