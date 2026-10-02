"""Final scanner rows survive background reads; calendar forecasts are not revisions."""
import json

import pytest

from test_frontend_scanner_lifecycle import PURE, ROOT, SOURCE, evaluate, lifecycle, node_run


def test_calendar_forecast_changes_never_abort_the_initial_result_read():
    lifecycle("""
      const slow=defer(); queue.push({promise:slow.promise});
      const state={running:false,last_run:t1,last_attempt_at:t1,run_id:'same',
        schedule:{automatic_paused:false,reason:null,timezone:'America/New_York',
          next_eligible_at:'2026-10-02T06:41:20Z',scheduled_at:t1,slot_id:'old'}};
      render(options({schedulerState:state})); await settle();
      const unexpected=defer(); queue.push({promise:unexpected.promise},{promise:unexpected.promise});
      for(const stamp of ['2026-10-02T06:41:25Z','2026-10-02T06:41:30Z']) {
        render({...opts,schedulerState:{...state,next_run:stamp,schedule:{...state.schedule,
          next_eligible_at:stamp,scheduled_at:stamp,slot_id:stamp}}}); await settle();
      }
      assert.equal(requests.length,1); assert.equal(requests[0].init.signal.aborted,false);
      assert.equal(feed.loading,true); assert.equal(feed.hasLoaded,false);
      slow.resolve(response(200,payload(t2,'FINAL'))); await settle();
      assert.equal(feed.results[0].ticker,'FINAL'); assert.equal(feed.loading,false);
    """)


def test_confirmed_32_rows_keep_their_summary_during_coalesced_background_reads():
    lifecycle("""
      const final=payload(t1,'FINAL'); final.data=Array.from({length:32},(_,i)=>({ticker:'FINAL'+i}));
      queue.push({body:final}); render(options()); await settle();
      const summary=()=>scannerCompactEvidence({info:feed.info,loading:feed.loading,
        running:feed.isScanning,error:feed.error,hasLoaded:feed.hasLoaded,count:feed.results.length});
      const first=defer(); queue.push({promise:first.promise});
      const reading=feed.refresh(); await settle();
      assert.equal(feed.loading,false); assert.equal(summary().text,'32 Ergebnisse');
      const second=defer(); queue.push({promise:second.promise});
      const newest=feed.refresh(); await settle();
      assert.equal(requests.length,2); assert.equal(requests[1].init.signal.aborted,false);
      assert.equal(feed.results.length,32); assert.equal(feed.info.cached_at,t1);
      assert.equal(summary().text,'32 Ergebnisse');
      first.resolve(response(200,final)); await reading; await newest; await settle();
      assert.equal(feed.results.length,32); assert.equal(summary().text,'32 Ergebnisse');
      assert.equal(feed.info.cached_at,t1); assert.equal(timers.size,1);
      // All passive refreshes share the first read, then reconcile once.
      await timer();
      assert.equal(requests.length,3); assert.equal(requests[2].init.signal.aborted,false);
      assert.equal(feed.results.length,32); assert.equal(summary().text,'32 Ergebnisse');
      assert.equal(feed.info.cached_at,t1); assert.equal(feed.loading,false);
      second.resolve(response(200,{...final,cached_at:t2})); await settle();
      assert.equal(feed.results.length,32); assert.equal(feed.info.cached_at,t2);
      assert.equal(feed.loading,false); assert.equal(timers.size,0);
      queue.push({status:503,body:{}}); await feed.refresh(); await settle();
      assert.equal(feed.results.length,32); assert.equal(feed.info.cached_at,t2);
      assert.equal(summary().tone,'error'); assert.equal(timers.size,1);
      assert.equal(requests.filter(r=>r.init.method==='POST').length,0);
    """)


@pytest.mark.parametrize("changed", [
    {"last_run": "2026-09-08T10:05:00"},
    {"last_attempt_at": "2026-09-08T10:05:00"},
    {"run_id": "new-run"}, {"scan_error": "scan_failed"},
    {"running": True}, {"control": {"state": "paused"}},
])
def test_real_scheduler_result_run_and_status_changes_still_refresh(changed):
    lifecycle("""
      const state={last_run:t1,last_attempt_at:t1,run_id:'old-run',running:false,
        scan_error:null,control:{state:'running'}};
      queue.push({body:payload(t1,'OLD')}); render(options({schedulerState:state})); await settle();
      queue.push({body:payload(t2,'NEW')});
      render({...opts,schedulerState:{...state,...CHANGED}}); await settle();
      assert.equal(requests.length,2); assert.equal(feed.results[0].ticker,'NEW');
    """.replace("CHANGED", json.dumps(changed)))


@pytest.mark.parametrize("info,label,stamp", [
    ({"cached_at": "2026-09-08T10:00:00Z"}, "Ergebnisstand", "2026-09-08T10:00:00Z"),
    ({"cached_at": "2026-09-08T10:00:00Z", "partial": True}, "Zwischenstand", "2026-09-08T10:00:00Z"),
    ({"cached_at": "2026-09-08T10:00:00Z", "scan_error": "scan_failed"}, "Gespeicherter Altstand", "2026-09-08T10:00:00Z"),
    ({"cached_at": "2026-09-08T10:00:00Z", "scan_running": True}, "Gespeicherter Altstand", "2026-09-08T10:00:00Z"),
    ({"cached_at": "2026-09-08T10:00:00Z", "data_quality": {"cache_status": "stale"}}, "Ergebnisstand (veraltet)", "2026-09-08T10:00:00Z"),
    ({"cached_at": "2026-09-08T10:06:00Z"}, "Ergebnisstand", None),
    ({"cached_at": "broken"}, "Ergebnisstand", None),
    ({}, "Ergebnisstand", None),
])
def test_result_timing_uses_the_displayed_snapshot_without_future_time_claims(info, label, stamp):
    result = evaluate(f"scannerResultTiming({json.dumps(info)}, Date.parse('2026-09-08T10:05:00Z'))")
    assert result == {"label": label, "timestamp": stamp}


def test_stock_and_bi_controls_receive_the_same_snapshot_as_their_tables():
    scanner = SOURCE[SOURCE.index("function ScannerTab("):SOURCE.index("function BIScannerTab(")]
    bi = SOURCE[SOURCE.index("function BIScannerTab("):SOURCE.index("function BiotechTab(")]
    assert "resultInfo={scanInfo}" in scanner
    assert "resultInfo={liveInfo}" in bi
    control = SOURCE[SOURCE.index("function ScanControl("):SOURCE.index("function LiveScanStatus(")]
    assert "scannerResultTiming(resultInfo)" in control
    assert "'Owner-Abschluss'" in control


def test_control_result_age_is_not_borrowed_from_the_scheduler_owner():
    component = SOURCE[SOURCE.index("function ScanControl("):SOURCE.index("function LiveScanStatus(")]
    node_run("""
const assert=require('node:assert/strict');
const babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const component=babel.transform(""" + json.dumps(component) + """,{presets:['react'],sourceType:'script'}).code;
const React={useContext:()=>false,createElement:(type,props,...children)=>({type,props:props||{},children})};
const render=new Function('React','useState','useEffect','getScanTiming','getRelativeTime','formatCacheAge',
"const API='';const ScannerAdminContext={};const useRef=v=>({current:v});\\n" + """ + json.dumps(PURE) + """ + component + '\\nreturn ScanControl;')(
React,initial=>[initial,()=>{}],()=>{},()=>({lastScan:'vor 12h'}),stamp=>'AGE:'+stamp,()=> 'QA');
function text(n){return Array.isArray(n)?n.map(text).join(' '):n==null||typeof n==='boolean'?'':typeof n==='object'?text(n.children):String(n);}
const base={onScan:()=>{},schedulerStatus:{},scanKey:'strategy_scan',controlFeed:{control:null}};
const result=text(render({...base,resultInfo:{cached_at:'2026-09-08T10:05:00Z'}}));
assert.match(result,/Ergebnisstand\\s*:\\s*AGE:2026-09-08T10:05:00Z/); assert.ok(!result.includes('vor 12h'));
const owner=text(render(base)); assert.match(owner,/Owner-Abschluss\\s*:\\s*vor 12h/);
const future=text(render({...base,resultInfo:{cached_at:'9999-01-01T00:00:00Z'}}));
assert.match(future,/Ergebnisstand\\s*:\\s*Zeitpunkt nicht bestätigt/); assert.ok(!future.includes('AGE:'));
""")


def test_future_scheduler_completion_is_not_shown_as_just_completed():
    helpers = SOURCE[SOURCE.index("function getRelativeTime("):SOURCE.index("function formatCacheAge(")]
    node_run(PURE + helpers + """
const assert=require('node:assert/strict');
assert.equal(getScanTiming({scans:{strategy_scan:{last_run:'9999-01-01T00:00:00Z'}}},'strategy_scan').lastScan,null);
assert.equal(getScanTiming({scans:{strategy_scan:{last_run:'broken'}}},'strategy_scan').lastScan,null);
""")
