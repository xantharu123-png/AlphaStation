"""Dedicated scan controls require fresh owner evidence, not cached rows."""
import json

import pytest

from test_frontend_scanner_lifecycle import SOURCE, PURE, HARNESS, ROOT, evaluate, node_run


def run(code):
    harness = HARNESS.replace("feed = useScannerFeed(opts);", "feed = useDedicatedScanControl(opts);")
    setup = r"""
const cfg=extra=>({scanKey:'orb',enabled:true,isAdmin:true,statusUrl:'/status',controlUrl:'/control',...extra});
const ctl=(state='running',extra={})=>({supported:true,owner_scan_key:'orb',run_id:'run-1',
 state,worker_alive:state!=='finished',scope:'scanner',resume_policy:'restart_fresh',...extra});
const status=(control=ctl(),extra={})=>({scans:{orb:{run_id:control?.run_id,running:control?.worker_alive===true,control,...extra}}});
"""
    node_run(PURE + harness + setup + "\n(async()=>{\n" + code + "\n})().catch(e=>{console.error(e);process.exitCode=1});")


@pytest.mark.parametrize("owner", ["biotech", "bear", "turtle", "orb", "penny_stocks", "volume_spikes",
                                      "money_flow", "early_movers", "btc_divergenz", "crypto_explosion", "crypto_strat_bull_flag"])
def test_dedicated_owner_keys_require_same_valid_control_contract(owner):
    raw = {"supported": True, "owner_scan_key": owner, "run_id": "run-1", "state": "running",
           "worker_alive": True, "scope": "scanner", "resume_policy": "restart_fresh"}
    value = evaluate("scannerControlValue(" + json.dumps(raw) + ")")
    assert value["owner_scan_key"] == owner and value["resume_policy"] == "restart_fresh"


@pytest.mark.parametrize("strategy,key", [("Volume Surge", "volume_spikes"), ("Turtle Breakout", "turtle"),
    ("Early Movers", "early_movers"), ("Biotech", "biotech"), ("Bear", "bear"), ("Bear Flag", None),
    ("Momentum Breakout Long", None), ("Cup and Handle Breakout", None), ("BI_LONG", None)])
def test_strategy_alias_control_owner_matches_backend_routing(strategy, key):
    assert evaluate(f"scannerDedicatedOwner({json.dumps(strategy)},'stocks')") == key
    assert evaluate(f"scannerDedicatedOwner({json.dumps(strategy)},'crypto')") is None


def test_pause_ack_is_not_worker_confirmation_and_targets_existing_run():
    run("""
queue.push({body:status()});render(cfg());await settle();
assert.equal(feed.control.state,'running');
const after=defer(); queue.push({body:{status:'pause_requested',control:ctl('pause_requested')}},{promise:after.promise});
await feed.setControl('pause',true);await settle();
assert.equal(feed.control.state,'running');assert.equal(feed.controlPending,'pause');
assert.deepEqual(JSON.parse(requests.find(r=>r.url==='/control').init.body),{scanner:'orb',run_id:'run-1',action:'pause',auto_resume:true});
after.resolve(response(200,status(ctl('paused'))));await settle();
assert.equal(feed.control.state,'paused');assert.equal(feed.controlPending,null);
""")


def test_control_timeout_clears_only_after_owner_bound_target_state_is_observed():
    run("""
let now=1000;Date.now=()=>now;
queue.push({body:status()});render(cfg());await settle();
queue.push({body:{status:'pause_requested',control:ctl('pause_requested')}},{body:status()});
await feed.setControl('pause',true);await settle();
assert.equal(feed.control.state,'running');assert.equal(feed.controlPending,'pause');
now+=16001;queue.push({body:status()});await feed.refresh();await settle();
assert.match(feed.controlError,/noch nicht durch den Worker/);assert.equal(feed.controlPending,null);
queue.push({body:status()});await feed.refresh();await settle();
assert.match(feed.controlError,/noch nicht durch den Worker/);
queue.push({body:status(ctl('paused'))});await feed.refresh();await settle();
assert.equal(feed.control.state,'paused');assert.equal(feed.controlError,null);
""")


def test_wrong_owner_or_conflicting_run_cannot_authorize_post():
    for override in ({"run_id": "other"}, {"control": {"supported": True, "owner_scan_key": "bi_long", "run_id": "run-1",
                                                     "state": "running", "worker_alive": True, "scope": "scanner"}}):
        run("queue.push({body:status(ctl()," + json.dumps(override) + ")});render(cfg());await settle();"
            "assert.equal(feed.control,null);await feed.setControl('pause');assert.equal(requests.filter(r=>r.url==='/control').length,0);")


def test_connection_failure_revokes_control_and_recovery_clears_connection_error():
    run("""
queue.push({body:status()});render(cfg());await settle();
queue.push({status:503,body:{detail:'SECRET'}});await feed.refresh();await settle();
assert.equal(feed.control,null);assert.match(feed.controlError,/Workerstatus/);assert.ok(!feed.controlError.includes('SECRET'));
queue.push({body:status()});await feed.refresh();await settle();
assert.equal(feed.control.state,'running');assert.equal(feed.controlError,null);
""")


@pytest.mark.parametrize("worker", [None, [], {"running": "false"}, {"running": None}])
def test_missing_or_malformed_worker_never_becomes_verified_idle(worker):
    run("""
queue.push({body:{scans:{orb:""" + json.dumps(worker) + """}}});render(cfg());await settle();
assert.equal(feed.control,null);assert.notEqual(feed.statusVerified,true);assert.match(feed.controlError,/Workerstatus/);
await feed.setControl('pause');assert.equal(requests.filter(r=>r.url==='/control').length,0);
""")


def test_non_admin_never_posts_and_protected_monitor_has_explicit_reason():
    run("""
queue.push({body:status()});render(cfg({isAdmin:false}));await settle();
await feed.setControl('pause');assert.equal(requests.length,1);
queue.push({body:{scans:{orb:{running:true,control:{supported:false,unsupported_reason:'protection_monitor',protected:true}}}}});
await feed.refresh();await settle();assert.equal(feed.control,null);assert.equal(feed.protectedReason,'protection_monitor');
assert.match(scannerProtectionNotice(feed.protectedReason),/Schutz/);
""")


def test_admin_downgrade_without_unmount_revokes_post_permission_immediately():
    run("""
queue.push({body:status()});render(cfg({isAdmin:true}));await settle();
assert.equal(feed.control.state,'running');
render(cfg({isAdmin:false}));await settle();
await feed.setControl('pause');await settle();
assert.equal(requests.filter(r=>r.url==='/control').length,0);
""")


def test_scope_change_discards_late_get_and_post():
    run("""
queue.push({body:status()});render(cfg());await settle();
const pendingPost=defer();queue.push({promise:pendingPost.promise});const post=feed.setControl('pause');await settle();
queue.push({body:{scans:{bear:{run_id:'bear-1',running:true,control:ctl('running',{owner_scan_key:'bear',run_id:'bear-1'})}}}});
render(cfg({scanKey:'bear'}));await settle();
pendingPost.resolve(response(200,{status:'pause_requested',control:ctl('pause_requested')}));await post;await settle();
assert.equal(feed.control.owner_scan_key,'bear');assert.equal(feed.controlNotice,null);
assert.equal(requests.find(r=>r.url==='/control').init.signal.aborted,true);
""")


def test_run_replacement_during_post_never_applies_old_ack():
    run("""
queue.push({body:status()});render(cfg());await settle();
const pendingPost=defer();queue.push({promise:pendingPost.promise});const post=feed.setControl('pause');await settle();
queue.push({body:status(ctl('running',{run_id:'run-2'}))});await feed.refresh();await settle();
queue.push({body:status(ctl('running',{run_id:'run-2'}))});
pendingPost.resolve(response(200,{status:'pause_requested',control:ctl('pause_requested')}));await post;await settle();
assert.equal(feed.control.run_id,'run-2');assert.equal(feed.controlPending,null);assert.equal(feed.controlNotice,null);
""")


@pytest.mark.parametrize("parent_status", [None, {"scans": {}}])
def test_verified_standalone_running_renders_without_parent_scheduler_snapshot(parent_status):
    _render_scan_control_with_standalone(
        parent_status=parent_status,
        standalone={"statusVerified": True, "observedRunning": True, "runningSinceSec": 7,
                    "control": None, "progress": None},
        is_scanning=False,
        expected_disabled=True,
        expected_label="Auto-Scan läuft...",
    )


def test_verified_standalone_finished_overrides_stale_parent_running_flags():
    _render_scan_control_with_standalone(
        parent_status={"scans": {"orb": {"running": True, "running_since_sec": 99}}},
        standalone={"statusVerified": True, "observedRunning": False, "runningSinceSec": 0,
                    "control": None, "progress": None},
        is_scanning=True,
        expected_disabled=False,
        expected_label="Scan starten",
    )


def _render_scan_control_with_standalone(*, parent_status, standalone, is_scanning,
                                         expected_disabled, expected_label):
    component = SOURCE[SOURCE.index("function ScanControl({"):SOURCE.index("function LiveScanStatus(")]
    component = component.replace("useDedicatedScanControl", "useDedicatedScanControlFixture")
    node_run("""
const assert=require('node:assert/strict');
const babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const component=babel.transform(""" + json.dumps(component) + """,{presets:['react'],sourceType:'script'}).code;
const standalone=""" + json.dumps(standalone) + """;
const React={useContext:()=>true,createElement:(type,props,...children)=>({type,props:props||{},children})};
const render=new Function('React','useState','useEffect','getScanTiming','formatCacheAge','useDedicatedScanControlFixture',
"const API='';const ScannerAdminContext={};const useRef=v=>({current:v});\\n" + """ + json.dumps(PURE) + """ + component + '\\nreturn ScanControl;')(
React,initial=>[initial,()=>{}],()=>{},()=>({}),()=> 'QA',()=>standalone);
const tree=render({onScan:async()=>{},isScanning:""" + json.dumps(is_scanning) + """,schedulerStatus:"""
             + json.dumps(parent_status) + """,scanKey:'orb'});
function nodes(n){return Array.isArray(n)?n.flatMap(nodes):!n||typeof n!=='object'?[]:[n,...nodes(n.children)];}
function text(n){return Array.isArray(n)?n.map(text).join(''):n==null||typeof n==='boolean'?'':typeof n==='object'?text(n.children):String(n);}
const start=nodes(tree).find(n=>n.props.className?.includes('scan-action-button'));
assert.ok(start);assert.equal(start.props.disabled,""" + json.dumps(expected_disabled) + """);
assert.equal(text(start.children[1]),""" + json.dumps(expected_label) + """);
""")
