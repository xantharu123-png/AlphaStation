"""Discoverable pause/resume actions and genuinely centered scanner labels."""
import json

import pytest

from test_frontend_scanner_lifecycle import SOURCE, PURE, ROOT, evaluate, node_run
from test_frontend_scan_control import control


def action(raw, **options):
    return evaluate(f"scannerControlAction({json.dumps(raw)}, {json.dumps(options)})")


def test_active_admin_has_explicit_stop_pause_and_paused_resume():
    assert action(control(), running=True, canControl=True) == {
        "label": "Stop / Pause", "action": "pause", "enabled": True, "reason": "",
    }
    assert action(control("paused"), running=True, canControl=True) == {
        "label": "Fortsetzen", "action": "resume", "enabled": True, "reason": "",
    }


@pytest.mark.parametrize("raw", [None, {}, control(run_id=None), control(supported=False)])
def test_unverified_control_is_visible_but_disabled_with_explanation(raw):
    result = action(raw, running=True, canControl=True)
    assert result["label"] == "Stop / Pause" and result["enabled"] is False
    assert "noch nicht bestaetigt" in result["reason"]


@pytest.mark.parametrize("admin", [False, None, "true", 1])
def test_nonadmin_is_not_silently_hidden_and_never_gains_permission(admin):
    result = action(control(), running=True, canControl=admin)
    assert result["enabled"] is False and "Administrator" in result["reason"]


@pytest.mark.parametrize("state", ["pause_requested", "finishing", "restart_required"])
def test_non_actionable_control_remains_disabled(state):
    result = action(control(state), running=True, canControl=True)
    assert result["enabled"] is False and result["reason"]


@pytest.mark.parametrize("pending,label", [("pause", "Pause angefordert..."), ("resume", "Fortsetzung angefordert...")])
def test_pending_actions_cannot_be_submitted_twice(pending, label):
    result = action(control(), running=True, canControl=True, pending=pending)
    assert result["enabled"] is False and result["label"] == label


def test_idle_has_no_fake_stop():
    assert action(None, canControl=True) is None
    assert action(control("finished", worker_alive=False), canControl=True) is None


@pytest.mark.parametrize("admin,verified,paused", [(True, True, False), (True, False, False),
                                                  (False, True, False), (True, True, True)])
def test_real_component_routes_only_verified_authorized_actions(admin, verified, paused):
    component = SOURCE[SOURCE.index("function ScanControl({"):SOURCE.index("function LiveScanStatus(")]
    ctl = control("paused" if paused else "running") if verified else None
    node_run("""
const assert=require('node:assert/strict');
const babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const component=babel.transform(""" + json.dumps(component) + """,{presets:['react'],sourceType:'script'}).code;
const React={useContext:()=>false,createElement:(type,props,...children)=>({type,props:props||{},children})};
const render=new Function('React','useState','useEffect','getScanTiming','formatCacheAge',
"const API='';const ScannerAdminContext={};const useRef=v=>({current:v});\\n" + """ + json.dumps(PURE) + """ + component + '\\nreturn ScanControl;')(
React,initial=>[initial,()=>{}],()=>{},()=>({}),()=> 'QA');
const requests=[],refreshes=[];
const tree=render({onScan:()=>{},isScanning:true,canControl:""" + json.dumps(admin) + """,
controlFeed:{control:""" + json.dumps(ctl) + """,controlPending:null,
setControl:(...args)=>requests.push(args),refresh:()=>refreshes.push(true)}});
function nodes(n){return Array.isArray(n)?n.flatMap(nodes):!n||typeof n!=='object'?[]:[n,...nodes(n.children)];}
function text(n){return Array.isArray(n)?n.map(text).join(''):n==null||typeof n==='boolean'?'':typeof n==='object'?text(n.children):String(n);}
const all=nodes(tree),start=all.find(n=>n.props.className?.includes('scan-action-button'));
const stop=all.find(n=>n.props.className?.includes('scan-stop-button'));
assert.equal(start.props.disabled,true);
assert.equal(start.children.length,3); // Equal-width left and right slots, label centered between them.
assert.equal(start.children[1].props.className,'scan-action-label');
assert.equal(start.children[0].props['aria-hidden'],'true');
assert.equal(start.children[2].props['aria-hidden'],'true');
assert.ok(stop);
const allowed=""" + json.dumps(admin and verified) + """;
assert.equal(stop.props.disabled,!allowed);
stop.props.onClick();
assert.equal(requests.length,allowed?1:0);
if(allowed)assert.deepEqual(requests[0],[""" + json.dumps("resume" if paused else "pause") + """,true]);
if(!""" + json.dumps(verified) + """ && """ + json.dumps(admin) + """) {
 const refresh=all.find(n=>n.type==='button'&&text(n)==='Status neu laden');
 assert.ok(refresh); refresh.props.onClick(); assert.equal(refreshes.length,1);
}
""")


def test_scan_label_has_balanced_grid_not_spinner_offset():
    css = SOURCE[SOURCE.index(".scan-action-button {"):SOURCE.index(".button-secondary {")]
    assert "grid-template-columns: 16px minmax(0, 1fr) 16px" in css
    assert "align-items: center" in css and "text-align: center" in css
    assert "grid-column: 2" in css


def test_pattern_progress_separates_selected_validation_from_universe_prefilter():
    info = {"partial": True, "scan_running": True, "checked": 12590, "total": 12590, "diagnostics": {
        "runtime_phase": "special_filter", "special_filter_input_count": 1200,
        "special_filter_checked_count": 12, "special_filter_unexamined_count": 1188,
        "special_filter_limit": 180,
    }}
    result = evaluate(f"scannerPatternProgress({json.dumps(info)}, 2, true)")
    assert (result["checked"], result["total"], result["hits"]) == (12, 180, 2)
    assert result["running"] is True and "Vorauswahl" in result["detail"]
    assert "12/180" in result["detail"]


@pytest.mark.parametrize("key,value", [("special_filter_checked_count", 181),
    ("special_filter_input_count", 9), ("special_filter_unexamined_count", -1),
    ("special_filter_limit", 0), ("special_filter_limit", True),
    ("special_filter_checked_count", "12"), ("runtime_phase", "analyzing")])
def test_invalid_pattern_progress_is_not_promoted_to_live_stage(key, value):
    diag = {"runtime_phase": "special_filter", "special_filter_input_count": 163,
            "special_filter_checked_count": 12, "special_filter_unexamined_count": 151,
            "special_filter_limit": 180, key: value}
    assert evaluate(f"scannerPatternProgress({json.dumps({'partial': True, 'scan_running': True, 'diagnostics': diag})}, 0, true)") is None


def test_final_cache_is_not_shown_as_ongoing_special_phase():
    assert evaluate("scannerPatternProgress({partial:false,scan_running:true,diagnostics:{runtime_phase:'special_filter'}}, 0, true)") is None


@pytest.mark.parametrize("running,server_running,error", [(False, True, None), (None, True, None),
    (True, False, None), (True, True, "scan_data_invalid"), (True, True, "scan_timeout")])
def test_stale_or_terminal_partial_never_disables_start_as_live_progress(running, server_running, error):
    info = {"partial": True, "scan_running": server_running, "scan_error": error, "diagnostics": {
        "runtime_phase": "special_filter", "special_filter_input_count": 163,
        "special_filter_checked_count": 12, "special_filter_unexamined_count": 151,
        "special_filter_limit": 180,
    }}
    assert evaluate(f"scannerPatternProgress({json.dumps(info)}, 1, {json.dumps(running)})") is None
