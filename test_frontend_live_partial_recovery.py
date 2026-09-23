"""A live watchdog warning is not a completed scan or permission to relax rows."""
import json

import pytest

from test_frontend_scanner_lifecycle import evaluate, lifecycle


def live_payload(**updates):
    return {
        "partial": True, "scan_running": True, "scan_error": "scan_timeout", "scan_run_id": "run-1",
        "scan_control": {
            "supported": True, "owner_scan_key": "strat_cup_and_handle_breakout", "run_id": "run-1",
            "state": "running", "worker_alive": True, "scope": "scanner",
        }, **updates,
    }


@pytest.mark.parametrize("owner", ["bi_long", "bi_short", "strategy_scan",
                                    "strat_cup_and_handle_breakout", "strat_wyckoff_accumulation",
                                    "crypto_strat_test"])
def test_verified_live_timeout_is_nonterminal_but_explicitly_warned(owner):
    payload = live_payload()
    payload["scan_control"]["owner_scan_key"] = owner
    encoded = json.dumps(payload)
    assert evaluate(f"scannerLivePartialTimeout({encoded}, 'run-1')") is True
    assert evaluate(f"scannerPollOutcome({encoded}, null, true, 'run-1')") == "running"
    state = evaluate(f"scannerEvidenceState({{info:{encoded},error:null,hasLoaded:true,count:2}})")
    assert state["tone"] == "warning"
    assert "Zeitbudget" in state["text"] and "Zwischenstand" in state["text"]
    assert "kein abgeschlossener Scan" in state["text"]


@pytest.mark.parametrize("updates", [
    {"partial": False}, {"scan_running": False}, {"scan_run_id": None},
    {"scan_error": "scan_data_invalid"}, {"scan_error": "scan_provider_unauthorized"},
    {"scan_control": None},
    {"scan_control": {**live_payload()["scan_control"], "worker_alive": False}},
    {"scan_control": {**live_payload()["scan_control"], "supported": False}},
    {"scan_control": {**live_payload()["scan_control"], "state": "finished"}},
    {"scan_control": {**live_payload()["scan_control"], "run_id": "different"}},
    {"scan_control": {**live_payload()["scan_control"], "owner_scan_key": "unsupported_test"}},
])
def test_no_live_exception_without_complete_same_worker_proof(updates):
    encoded = json.dumps(live_payload(**updates))
    assert evaluate(f"scannerLivePartialTimeout({encoded}, 'run-1')") is False
    assert evaluate(f"scannerPollOutcome({encoded}, null, true, 'run-1')") == "error"


def test_expected_run_mismatch_precedes_live_timeout_exception():
    encoded = json.dumps(live_payload())
    assert evaluate(f"scannerLivePartialTimeout({encoded}, 'other')") is False
    assert evaluate(f"scannerPollOutcome({encoded}, null, true, 'other')") == "superseded"


@pytest.mark.parametrize("owner", [
    "biotech", "bear", "turtle", "orb", "penny_stocks", "volume_spikes",
    "money_flow", "early_movers", "btc_divergenz", "crypto_explosion",
])
def test_pause_capability_does_not_grant_live_partial_timeout_exception(owner):
    payload = live_payload()
    payload["scan_control"]["owner_scan_key"] = owner
    payload["scan_control"]["resume_policy"] = "restart_fresh"
    encoded = json.dumps(payload)
    assert evaluate(f"scannerControlSnapshot({encoded}).supported") is True
    assert evaluate(f"scannerLivePartialTimeout({encoded}, 'run-1')") is False
    assert evaluate(f"scannerPollOutcome({encoded}, null, true, 'run-1')") == "error"


BASE = """
const ctl=(extra={})=>({supported:true,owner_scan_key:'bi_long',run_id:'run-1',state:'running',
  worker_alive:true,scope:'scanner',...extra});
const live=(ticker='LIVE',extra={})=>payload(t2,ticker,{partial:true,scan_running:true,
  scan_error:'scan_timeout',scan_run_id:'run-1',scan_control:ctl(),...extra});
const cached={scopeKey:'bi:long',items:[{ticker:'FINAL'}],info:scannerSnapshotInfo(payload(t1,'FINAL'))};
const writes=[];
const configured=extra=>options({controlEnabled:true,isAdmin:true,controlUrl:'/control',
  scanCache:{'bi:snapshot:bi:long':cached},updateScanCache:(...args)=>writes.push(args),...extra});
"""


def test_live_partial_rows_survive_warning_continue_polling_and_wait_for_final():
    lifecycle(BASE + """
      queue.push({body:live()}); render(configured()); await settle();
      assert.equal(feed.results[0].ticker,'LIVE'); assert.equal(feed.info.partial,true);
      assert.equal(feed.info.scan_error,'scan_timeout'); assert.equal(feed.error,null);
      assert.equal(feed.isScanning,true); assert.equal(timers.size,1);
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,0);
      for (let i=0;i<3;i++) {
        queue.push({body:live('NEXT'+i)}); await timer();
        assert.equal(feed.results[0].ticker,'NEXT'+i); assert.equal(feed.isScanning,true);
        assert.equal(feed.error,null); assert.equal(timers.size,1);
      }
      assert.equal(toasts.length,0);
      queue.push({body:payload('2026-09-08T10:10:00','COMPLETE',{scan_run_id:'run-1',
        scan_control:ctl({state:'finished',worker_alive:false})})}); await timer();
      assert.equal(feed.results[0].ticker,'COMPLETE'); assert.equal(feed.info.partial,false);
      assert.equal(feed.isScanning,false); assert.equal(feed.error,null); assert.equal(timers.size,0);
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,1);
    """)


@pytest.mark.parametrize("failure", [
    {"scan_running": False, "scan_control": {**live_payload()["scan_control"], "worker_alive": False}},
    {"scan_control": None}, {"scan_error": "scan_data_invalid"},
    {"scan_error": "scan_provider_unauthorized"},
    {"scan_control": {**live_payload()["scan_control"], "run_id": "wrong"}},
])
def test_terminal_or_unproven_partial_restores_last_final_rows(failure):
    lifecycle(BASE + """
      queue.push({body:live()}); render(configured()); await settle();
      assert.equal(feed.results[0].ticker,'LIVE');
      queue.push({body:live('UNVERIFIED',""" + json.dumps(failure) + """)}); await timer();
      assert.equal(feed.results[0].ticker,'FINAL'); assert.equal(feed.info.cached_at,t1);
      assert.equal(feed.isScanning,false); assert.ok(feed.error); assert.equal(timers.size,0);
      assert.equal(toasts.length,0);
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,0);
    """)


def test_transport_failure_after_live_timeout_keeps_final_not_partial_snapshot():
    lifecycle(BASE + """
      queue.push({body:live()}); render(configured()); await settle();
      queue.push({status:503,body:{detail:'PRIVATE'}}); await timer();
      assert.equal(feed.results[0].ticker,'FINAL'); assert.equal(feed.control,null);
      assert.equal(feed.isScanning,false); assert.equal(timers.size,0);
      assert.ok(feed.error); assert.ok(!feed.error.includes('PRIVATE'));
    """)


@pytest.mark.parametrize("successor_partial", [False, True])
def test_superseded_poll_retains_final_rows_but_updates_verified_control_identity(successor_partial):
    lifecycle(BASE + """
      queue.push({body:live()}); render(configured()); await settle();
      const partial=""" + json.dumps(successor_partial) + """;
      queue.push({body:live('OTHER-RUN',{partial,scan_error:partial?'scan_timeout':null,
        scan_run_id:'run-2',scan_control:ctl({run_id:'run-2'})})}); await timer();
      assert.equal(feed.results[0].ticker,'FINAL'); assert.equal(feed.info.cached_at,t1);
      assert.equal(feed.info.scan_run_id,'run-2'); assert.equal(feed.control.run_id,'run-2');
      assert.equal(feed.isScanning,false); assert.match(feed.error,/anderer Scanlauf/);
      assert.equal(timers.size,0); assert.equal(toasts.length,0);
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,0);
    """)


def test_empty_live_partial_is_not_final_zero_and_never_reuses_old_hit_count():
    lifecycle(BASE + """
      queue.push({body:live(null)}); render(configured()); await settle();
      assert.equal(feed.results.length,0); assert.equal(feed.info.partial,true);
      assert.equal(feed.isScanning,true); assert.equal(toasts.length,0);
      const state=scannerEvidenceState({info:feed.info,error:feed.error,hasLoaded:true,count:0});
      assert.equal(state.tone,'warning'); assert.match(state.text,/Zwischenstand/);
      assert.ok(!state.text.includes('Abgeschlossener Scan'));
    """)


@pytest.mark.parametrize("failure", ["http_503", "http_401", "network", "malformed_rows"])
def test_first_ever_partial_is_discarded_after_read_failure(failure):
    lifecycle(BASE + """
      queue.push({body:live()}); render(configured({scanCache:{}})); await settle();
      assert.equal(feed.results[0].ticker,'LIVE'); assert.equal(feed.info.partial,true);
      const failure=""" + json.dumps(failure) + """;
      if (failure==='network') queue.push({error:true});
      else if (failure==='malformed_rows') queue.push({body:live(null,{data:[null]})});
      else queue.push({status:failure==='http_503'?503:401,body:{detail:'PRIVATE'}});
      await timer();
      assert.equal(feed.results.length,0); assert.equal(feed.info,null); assert.equal(feed.control,null);
      assert.equal(feed.isScanning,false); assert.equal(timers.size,0); assert.ok(feed.error);
      assert.ok(!feed.error.includes('PRIVATE')); assert.equal(toasts.length,0);
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,0);
      const evidence=scannerEvidenceState({info:feed.info,error:feed.error,hasLoaded:true,count:0});
      assert.equal(evidence.tone,'error');
    """)


@pytest.mark.parametrize("failure", [
    {"scan_running": False, "scan_control": {**live_payload()["scan_control"], "worker_alive": False}},
    {"scan_control": None}, {"scan_error": "scan_data_invalid"},
    {"scan_error": "scan_provider_unauthorized"}, {"scan_run_id": None},
    {"scan_control": {**live_payload()["scan_control"], "run_id": "wrong"}},
])
def test_first_ever_failed_partial_has_no_rows_timestamp_or_stale_progress(failure):
    lifecycle(BASE + """
      queue.push({body:live('LIVE',{checked:19,total:100,progress_detail:'OLD PROGRESS'})});
      render(configured({scanCache:{}})); await settle();
      queue.push({body:live('REJECTED',{checked:23,total:100,progress_detail:'INVALID PROGRESS',
        ...""" + json.dumps(failure) + """})}); await timer();
      assert.equal(feed.results.length,0); assert.ok(feed.error); assert.equal(feed.isScanning,false);
      assert.equal(feed.info.cached_at ?? null,null); assert.ok(!feed.info.partial);
      assert.equal(feed.info.checked,undefined); assert.equal(feed.info.total,undefined);
      assert.equal(feed.info.progress_detail,undefined); assert.equal(timers.size,0);
      assert.equal(toasts.length,0);
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,0);
    """)


@pytest.mark.parametrize("has_final", [False, True])
@pytest.mark.parametrize("failure", ["missing_final", "incomplete_coverage", "restart_required"])
def test_terminal_poll_never_promotes_its_rejected_response_to_final_cache(has_final, failure):
    lifecycle(BASE + """
      const hasFinal=""" + json.dumps(has_final) + """;
      queue.push({body:live()}); render(configured({scanCache:hasFinal?{'bi:snapshot:bi:long':cached}:{}}));
      await settle();
      const failure=""" + json.dumps(failure) + """;
      if (failure==='restart_required') queue.push({body:live('INVALIDATED',{
        scan_running:false,scan_control:ctl({state:'restart_required',worker_alive:false})})});
      else queue.push({body:payload(failure==='missing_final'?t2:'2026-09-08T10:10:00','UNVERIFIED',{
        scan_run_id:'run-1',scan_control:ctl({state:'finished',worker_alive:false}),
        diagnostics:{coverage:failure==='incomplete_coverage'?'incomplete':'complete'}})});
      await timer();
      assert.deepEqual(feed.results,hasFinal?[{ticker:'FINAL'}]:[]);
      assert.equal(feed.info.cached_at ?? null,hasFinal?t1:null); assert.ok(!feed.info.partial);
      assert.equal(feed.isScanning,false); assert.equal(timers.size,0);
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,0);
      assert.equal(toasts.length,0);
      if (failure==='restart_required') {
        assert.equal(feed.error,null); assert.match(feed.notice,/neuer Scan erforderlich/);
        assert.equal(feed.control.state,'restart_required');
      } else assert.ok(feed.error);
      // A later HTTP failure must not resurrect the rejected terminal rows.
      queue.push({status:503,body:{}}); await feed.refresh(); await settle();
      assert.deepEqual(feed.results,hasFinal?[{ticker:'FINAL'}]:[]);
      assert.equal(feed.info?.cached_at ?? null,hasFinal?t1:null);
    """)


@pytest.mark.parametrize("partial", [False, True])
def test_first_ever_superseded_poll_discards_rows_but_retains_new_control(partial):
    lifecycle(BASE + """
      queue.push({body:live()}); render(configured({scanCache:{}})); await settle();
      const partial=""" + json.dumps(partial) + """;
      queue.push({body:live('OTHER-RUN',{partial,scan_error:partial?'scan_timeout':null,
        scan_run_id:'run-2',scan_control:ctl({run_id:'run-2'})})}); await timer();
      assert.equal(feed.results.length,0); assert.ok(!feed.info.partial);
      assert.equal(feed.info.cached_at,undefined); assert.equal(feed.info.scan_run_id,'run-2');
      assert.equal(feed.control.run_id,'run-2'); assert.match(feed.error,/anderer Scanlauf/);
      assert.equal(feed.isScanning,false); assert.equal(timers.size,0);
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,0);
    """)


def test_first_response_failed_partial_is_never_displayed_as_first_result():
    lifecycle(BASE + """
      queue.push({body:live('NEVER-SHOW',{scan_running:false,scan_error:'scan_data_invalid',
        scan_control:ctl({state:'finished',worker_alive:false})})});
      render(configured({scanCache:{}})); await settle();
      assert.equal(feed.results.length,0); assert.ok(!feed.info.partial); assert.ok(feed.error);
      assert.equal(feed.info.cached_at ?? null,null);
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,0);
    """)


@pytest.mark.parametrize("failure", ["http_403", "network", "unconfirmed_ack"])
def test_first_partial_observed_during_start_race_is_discarded_when_start_unconfirmed(failure):
    lifecycle(BASE + """
      queue.push({body:payload(null)}); render(configured({scanCache:{}})); await settle();
      queue.push({body:live()});
      const failure=""" + json.dumps(failure) + """;
      if (failure==='network') queue.push({error:true});
      else if (failure==='http_403') queue.push({status:403,body:{detail:'PRIVATE'}});
      else queue.push({body:{status:'unknown'}});
      await feed.start(); await settle();
      assert.equal(feed.results.length,0); assert.equal(feed.info,null); assert.equal(feed.control,null);
      assert.equal(feed.isScanning,false); assert.ok(feed.error); assert.equal(timers.size,0);
      assert.equal(toasts.length,0); assert.ok(!feed.error.includes('PRIVATE'));
      assert.equal(writes.filter(([key])=>key==='bi:snapshot:bi:long').length,0);
    """)
