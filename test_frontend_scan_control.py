"""Server-confirmed stock pause controls, with no browser/provider access."""
import json

import pytest

from test_frontend_scanner_lifecycle import SOURCE, PURE, ROOT, evaluate, lifecycle, node_run


def control(state="running", **updates):
    return {"supported": True, "owner_scan_key": "strat_cup_and_handle_breakout", "run_id": "run-1",
            "state": state, "worker_alive": state != "finished", "paused_seconds": 0,
            "auto_resume": True, "resume_at": "2026-09-21T13:30:00Z", "scope": "scanner", **updates}


def value(raw):
    return evaluate("scannerControlValue(" + json.dumps(raw) + ")")


@pytest.mark.parametrize("state,fragment", [("running", "Scan laeuft"), ("pause_requested", "Pause angefordert"),
    ("paused", "Pausiert"), ("finishing", "Abschluss laeuft"), ("restart_required", "neuer Scan")])
def test_control_state_uses_fixed_truthful_labels(state, fragment):
    result = evaluate("scannerControlPresentation(scannerControlValue(" + json.dumps(control(state)) + "))")
    assert fragment in result["text"]


@pytest.mark.parametrize("updates", [{"supported": False}, {"owner_scan_key": "crypto_strat_test"},
    {"owner_scan_key": "SECRET"}, {"run_id": None}, {"state": "SECRET"}, {"worker_alive": "yes"}])
def test_unknown_and_crypto_controls_are_not_offered(updates):
    assert value(control(**updates)) is None


def test_round_scope_is_explicit_and_resume_timestamp_is_sanitized():
    result = value(control("paused", owner_scan_key="strategy_scan", scope="strategy_round", resume_at="SECRET"))
    assert result["scope"] == "strategy_round" and result["resume_at"] is None
    assert "SECRET" not in json.dumps(result)


def test_conflicting_result_and_scheduler_run_never_choose_a_control_target():
    assert evaluate("scannerControlSnapshot(" + json.dumps({"scan_control": control()}) + ","
                    + json.dumps({"control": control(run_id="different")}) + ")") is None


def test_result_run_identity_blocks_old_scheduler_fallback_even_without_result_control():
    assert evaluate("scannerControlSnapshot({scan_run_id:'new-run'},"
                    + json.dumps({"control": control(run_id="old-run")}) + ")") is None


@pytest.mark.parametrize("state", ["pause_requested", "paused", "finishing", "restart_required"])
def test_live_control_never_becomes_final_zero_or_old_error(state):
    payload = {"scan_running": True, "partial": True, "data": [], "scan_error": "scan_timeout",
               "scan_run_id": "run-1", "scan_control": control(state), "cached_at": "2026-09-21T13:00:00Z"}
    result = evaluate("scannerPollOutcome(" + json.dumps(payload) + ",'2026-09-21T12:00:00Z',true,'run-1')")
    assert result in ("pause_requested", "paused", "finishing")


def test_restart_required_after_worker_exit_is_not_crash_or_final_zero():
    payload = {"scan_running": False, "partial": True, "data": [], "scan_control": control("restart_required", worker_alive=False)}
    assert evaluate("scannerPollOutcome(" + json.dumps(payload) + ",null,true)") == "restart_required"


def test_control_from_superseded_run_cannot_mask_new_run_identity():
    payload = {"scan_running": True, "scan_run_id": "different", "scan_control": control("paused", run_id="different")}
    assert evaluate("scannerPollOutcome(" + json.dumps(payload) + ",null,true,'run-1')") == "superseded"


def test_scan_snapshot_retains_control_and_schedule_contract():
    schedule = {"automatic_paused": True, "reason": "weekend", "timezone": "America/New_York",
                "next_eligible_at": "2026-09-21T04:00:00Z"}
    payload = {"scan_control": control("paused"), "scan_schedule": schedule}
    result = evaluate("scannerSnapshotInfo(" + json.dumps(payload) + ")")
    assert result["scan_control"]["state"] == "paused"
    assert result["scan_schedule"]["reason"] == "weekend"


def test_weekend_schedule_is_server_owned_and_manual_start_stays_allowed():
    schedule = {"automatic_paused": True, "reason": "weekend", "timezone": "America/New_York",
                "next_eligible_at": "2026-09-21T04:00:00Z"}
    result = evaluate("scannerScheduleNotice(" + json.dumps(schedule) + ")")
    assert "Wochenendpause" in result and "Manuelle Starts bleiben moeglich" in result
    assert "America/New_York" in result and "2026-09-21 04:00 UTC" in result
    assert evaluate("scannerScheduleNotice({automatic_paused:true,reason:'SECRET'})") is None


BASE_CONTROL_JS = """
const ctl=(state='running',extra={})=>({supported:true,owner_scan_key:'bi_long',run_id:'run-1',
 state,worker_alive:true,paused_seconds:0,auto_resume:true,resume_at:'2026-09-21T13:30:00Z',scope:'scanner',...extra});
const live=(state='running',extra={})=>payload(t1,'KEPT',{scan_running:true,scan_run_id:'run-1',scan_control:ctl(state),...extra});
const configured=extra=>options({controlEnabled:true,isAdmin:true,controlUrl:'/control',...extra});
"""


def test_pause_ack_is_not_optimistic_paused_and_request_is_run_bound():
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:live()}); render(configured()); await settle();
      queue.push({body:{status:'pause_requested',control:ctl('pause_requested')}});
      await feed.setControl('pause',true); await settle();
      assert.equal(feed.control.state,'running');
      const request=requests.find(r=>r.url==='/control');
      assert.deepEqual(JSON.parse(request.init.body),{scanner:'bi_long',run_id:'run-1',action:'pause',auto_resume:true});
      assert.match(feed.controlNotice,/angefordert/);
      queue.push({body:live('paused')}); await timer();
      assert.equal(feed.control.state,'paused'); assert.equal(feed.error,null);
      assert.equal(feed.isScanning,true); assert.equal(toasts.length,0);
    """)


def test_resume_ack_waits_for_observed_running_state():
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:live('paused')}); render(configured()); await settle();
      queue.push({body:{status:'resume_requested',control:ctl('paused')}});
      await feed.setControl('resume',true); await settle();
      assert.equal(feed.control.state,'paused');
      queue.push({body:live('running')}); await timer();
      assert.equal(feed.control.state,'running'); assert.equal(feed.controlError,null);
    """)


def test_late_control_ack_after_direction_change_cannot_mutate_new_scope():
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:live()}); render(configured()); await settle();
      const slow=defer(); queue.push({promise:slow.promise});
      const request=feed.setControl('pause',true); await settle();
      queue.push({body:payload(t2,'SHORT',{scan_control:ctl('running',{owner_scan_key:'bi_short',run_id:'short-1'})})});
      render(configured({scopeKey:'bi:short',resultsUrl:'/short'})); await settle();
      slow.resolve(response(200,{status:'pause_requested',control:ctl('pause_requested')}));
      await request; await settle();
      assert.equal(feed.results[0].ticker,'SHORT'); assert.equal(feed.controlNotice,null);
      assert.equal(requests.find(r=>r.url==='/control').init.signal.aborted,true);
    """)


def test_late_control_ack_after_new_run_is_not_applied():
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:live()}); render(configured()); await settle();
      const slow=defer(); queue.push({promise:slow.promise});
      const request=feed.setControl('pause',true); await settle();
      queue.push({body:live('running',{scan_run_id:'run-2',scan_control:ctl('running',{run_id:'run-2'})})});
      await timer();
      slow.resolve(response(200,{status:'pause_requested',control:ctl('pause_requested')}));
      await request; await settle();
      assert.equal(feed.control.run_id,'run-2'); assert.equal(feed.controlNotice,null);
    """)


@pytest.mark.parametrize("updates", [{"isAdmin": False}, {"controlEnabled": False}])
def test_non_admin_or_non_stock_never_posts_control(updates):
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:live()}); render(configured(""" + json.dumps(updates) + """)); await settle();
      await feed.setControl('pause',true); await settle();
      assert.equal(requests.filter(r=>r.url==='/control').length,0);
    """)


def test_server_denial_remains_visible_without_stopping_the_running_worker():
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:live()}); render(configured()); await settle();
      queue.push({status:403,body:{detail:'SECRET'}});
      await feed.setControl('pause',true); await settle();
      assert.match(feed.controlError,/Berechtigung/); assert.ok(!feed.controlError.includes('SECRET'));
      assert.equal(feed.control.state,'running'); assert.equal(feed.isScanning,true);
    """)


def test_mismatched_success_ack_never_changes_state_or_claims_pause():
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:live()}); render(configured()); await settle();
      queue.push({body:{status:'pause_requested',control:ctl('paused',{run_id:'different'})}});
      await feed.setControl('pause',true); await settle();
      assert.match(feed.controlError,/passt nicht/);
      assert.equal(feed.control.state,'running'); assert.equal(feed.controlNotice,null);
    """)


def test_finished_or_dead_worker_cannot_receive_resume_request():
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:payload(t1,'KEPT',{scan_control:ctl('paused',{worker_alive:false})})});
      render(configured()); await settle();
      await feed.setControl('resume',true); await settle();
      assert.equal(requests.filter(r=>r.url==='/control').length,0);
    """)


def test_disabled_control_does_not_hide_real_backend_error():
    payload = {"scan_error": "scan_data_invalid", "scan_control": control("paused", supported=False), "scan_running": False}
    assert evaluate("scannerPollOutcome(" + json.dumps(payload) + ",null,true)") == "error"


def test_old_control_run_does_not_hide_new_backend_error():
    payload = {"scan_error": "scan_data_invalid", "scan_control": control("paused"), "scan_run_id": "run-2"}
    assert evaluate("scannerPollOutcome(" + json.dumps(payload) + ",null,true)") == "error"


def test_retained_final_rows_never_restore_old_worker_control_metadata():
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:payload(t1,'OLD',{scan_run_id:'old-run',scan_control:ctl('finished',{run_id:'old-run',worker_alive:false})})});
      render(configured()); await settle();
      const schedule={automatic_paused:true,reason:'weekend',timezone:'America/New_York',next_eligible_at:'2026-09-21T04:00:00Z'};
      queue.push({body:payload(null,null,{partial:true,scan_running:true,scan_error:'scan_timeout',
        scan_run_id:'new-run',scan_control:ctl('paused',{run_id:'new-run'}),scan_schedule:schedule})});
      await feed.refresh(); await settle();
      assert.equal(feed.results[0].ticker,'OLD'); assert.equal(feed.info.cached_at,t1);
      assert.equal(feed.info.scan_run_id,'new-run'); assert.equal(feed.info.scan_running,true);
      assert.equal(feed.control.run_id,'new-run'); assert.equal(feed.control.state,'paused');
      assert.equal(feed.info.scan_schedule.reason,'weekend'); assert.equal(feed.error,null);
    """)


def test_dead_unconfirmed_pause_does_not_hide_real_error_banner():
    payload = {"info": {"scan_error": "scan_data_invalid", "scan_control": control("paused", worker_alive=False)},
               "error": None, "hasLoaded": True, "count": 0}
    assert evaluate("scannerEvidenceState(" + json.dumps(payload) + ")")["tone"] == "error"


def test_failed_poll_revokes_cached_worker_control_and_exposes_connection_error():
    lifecycle(BASE_CONTROL_JS + """
      queue.push({body:payload(t1,'OLD',{scan_run_id:'run-1',scan_control:ctl('paused')})});
      render(configured()); await settle();
      queue.push({status:503,body:{detail:'SECRET'}}); await feed.refresh(); await settle();
      assert.equal(feed.results[0].ticker,'OLD'); assert.equal(feed.control,null);
      assert.equal(scannerEvidenceState({info:feed.info,error:feed.error,hasLoaded:true,count:1}).tone,'error');
      await feed.setControl('resume',true); await settle();
      assert.equal(requests.filter(r=>r.url==='/control').length,0);
    """)


def test_shipped_buttons_are_admin_stock_only_and_warn_about_scope_and_lifetime():
    assert 'canControl={isAdmin}' in SOURCE
    assert 'controlEnabled: marketType === \'stocks\'' in SOURCE
    assert 'Pausiert die gesamte Aktienrunde' in SOURCE
    assert 'Automatisch beim naechsten Lauf fortsetzen' in SOURCE
    assert 'Nur im laufenden Serverprozess' in SOURCE
    assert 'controlFeed?.setControl' in SOURCE
    assert 'andere schwere Aktien-Scans' in SOURCE
    assert 'Krypto und Positionspflege werden dadurch nicht pausiert' in SOURCE


@pytest.mark.parametrize("expanded", [False, True])
def test_real_scheduler_component_renders_paused_and_running_workers(expanded):
    start = SOURCE.index("function SchedulerStatus({")
    component = SOURCE[start:SOURCE.index("// ScanControl Component", start)]
    status = {"scheduler_running": True, "scans": {
        "bi_long": {"running": True, "cache_health": "ok", "control": control("paused", owner_scan_key="bi_long")},
        "crypto_explosion": {"running": True, "cache_health": "ok"},
        "bi_short": {"running": False, "cache_health": "ok", "control": control("finished", owner_scan_key="bi_short")},
    }}
    node_run("""
      const assert=require('node:assert/strict');
      const babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
      const compiled=babel.transform(""" + json.dumps(component) + """, {presets:['react'],sourceType:'script'}).code;
      const React={createElement:(type,props,...children)=>({type,props,children})};
      const render=new Function('React','useState','getRelativeTime','formatCacheAge',
        """ + json.dumps(PURE) + """ + compiled + '\\nreturn SchedulerStatus;')(
          React,()=>[""" + json.dumps(expanded) + """,()=>{}],()=> 'QA-Zeit',()=> 'QA-Cache');
      const text=node=>Array.isArray(node)?node.map(text).join(''):node==null||typeof node==='boolean'?'':
        typeof node==='object'?text(node.children):String(node);
      assert.equal(render({status:null}),null);
      const output=text(render({status:""" + json.dumps(status) + """}));
      assert.match(output,/Auto-Scans: 3 \\(1 aktiv\\), 1 pausiert/);
      if (""" + json.dumps(expanded) + """) {
        assert.match(output,/BI LongPausiert/); assert.match(output,/Crypto Long Enginelaeuft/);
      }
    """)
