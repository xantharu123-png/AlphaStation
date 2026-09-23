"""Current-run progress is shared, finite and distinct from stored result rows."""
import json

import pytest

from test_frontend_scanner_lifecycle import SOURCE, PURE, ROOT, evaluate, node_run


STRATEGY = "Momentum Breakout Long"


def options():
    return {
        "strategy": STRATEGY, "scanKey": "strategy_scan", "running": True, "count": 7,
        "info": {"scan_run_id": "run-1", "scan_running": True, "partial": False,
                 "checked": 12590, "total": 12590, "cached_at": "2026-09-22T10:00:00Z",
                 "scan_last_attempt_at": "2026-09-23T10:00:00Z"},
        "schedulerState": {"run_id": "run-1", "running": True,
                           "progress": {"running": True, "strategy": "momentum_breakout_long",
                                        "phase": "analyzing", "checked": 7306, "total": 12590}},
    }


def selected(data):
    return evaluate(f"scannerSelectedProgress({json.dumps(data)})")


def live_options():
    data = options()
    data["info"].update(partial=True, cached_at="2026-09-23T10:02:00Z", checked=7000,
                        diagnostics={"strategy": STRATEGY})
    return data


@pytest.mark.parametrize("bad", [None, True, False, "12", -1, 0.5, 1000000001])
@pytest.mark.parametrize("key", ["checked", "total", "hits"])
def test_progress_counts_never_coerce_missing_invalid_or_unbounded_values(key, bad):
    raw = {"checked": 12, "total": 60, "hits": 3, key: bad}
    result = evaluate(f"scannerProgressValue({json.dumps(raw)})")
    if key == "hits":
        assert result["hits"] is None and result["hasProgress"] is True
    else:
        assert result["hasProgress"] is False and result["percent"] is None


@pytest.mark.parametrize("checked,total", [(1, 0), (0, 0), (61, 60)])
def test_impossible_denominator_or_overrun_is_unknown_not_false_full_bar(checked, total):
    assert evaluate(f"scannerProgressValue({{checked:{checked},total:{total}}})")["hasProgress"] is False


def test_nan_infinity_and_absent_hits_remain_unknown_but_real_zero_is_known():
    assert evaluate("scannerProgressValue({checked:NaN,total:Infinity,hits:Infinity})")["hits"] is None
    assert evaluate("scannerProgressValue({checked:0,total:60})")["percent"] == 0
    assert evaluate("scannerProgressValue({checked:0,total:60,hits:0})")["hits"] == 0


def test_screenshot_runtime_progress_does_not_borrow_old_final_counts_or_hits():
    result = selected(options())
    assert (result["checked"], result["total"], result["percent"]) == (7306, 12590, 58)
    assert result["hits"] is None and result["currentResult"] is False
    assert result["source"] == "runtime"


@pytest.mark.parametrize("key,value", [("run_id", "old-run"), ("running", False)])
def test_old_or_stopped_scheduler_does_not_contribute_counters(key, value):
    data = options()
    data["schedulerState"][key] = value
    result = selected(data)
    assert result["hasProgress"] is False and result["hits"] is None


@pytest.mark.parametrize("strategy", ["gap_momentum_long", "Cup and Handle Breakout", "", None])
def test_sibling_strategy_in_same_sweep_never_lends_counters_or_visible_hits(strategy):
    data = live_options()
    data["schedulerState"]["progress"]["strategy"] = strategy
    result = selected(data)
    assert result["hasProgress"] is False and result["currentResult"] is False
    assert result["hits"] is None


@pytest.mark.parametrize("updates", [
    {"scan_run_id": None}, {"cached_at": "2026-09-22T10:00:00Z"},
    {"scan_last_attempt_at": None}, {"cached_at": "broken"}, {"partial": False},
    {"scan_running": False},
])
def test_partial_count_requires_current_run_timestamp_not_just_running_flag(updates):
    data = live_options()
    data["info"].update(updates)
    result = selected(data)
    assert result["currentResult"] is False and result["hits"] is None


@pytest.mark.parametrize("failure", ["http_error", "scan_data_invalid", "scan_provider_unauthorized", "scan_timeout"])
def test_failed_or_unverified_timeout_cannot_resurrect_old_progress(failure):
    data = live_options()
    if failure == "http_error":
        data["error"] = "HTTP 503"
    else:
        data["info"]["scan_error"] = failure
    result = selected(data)
    assert result["hasProgress"] is False and result["hits"] is None


def test_same_worker_verified_live_timeout_keeps_truthful_current_partial_count():
    data = live_options()
    data["info"].update(scan_error="scan_timeout", scan_control={
        "supported": True, "owner_scan_key": "strategy_scan", "run_id": "run-1",
        "state": "running", "worker_alive": True, "scope": "strategy_round"})
    result = selected(data)
    assert result["currentResult"] is True and result["hits"] == 7
    assert result["hits_label"] == "sichtbare Treffer"


def test_pattern_stage_uses_its_own_denominator_instead_of_finished_universe():
    data = live_options()
    data["info"]["diagnostics"].update(runtime_phase="special_filter", special_filter_input_count=1200,
                                      special_filter_checked_count=12, special_filter_unexamined_count=1188,
                                      special_filter_limit=180)
    result = selected(data)
    assert (result["checked"], result["total"], result["percent"]) == (12, 180, 7)
    assert result["source"] == "pattern" and result["hits"] == 7


def test_bi_current_partial_is_used_but_unbound_scheduler_file_is_not():
    data = live_options()
    data.update(scanKey="bi_long", strategy=None)
    data["schedulerState"]["progress"] = {"status": "running", "checked": 900, "total": 900, "hits": 77}
    result = selected(data)
    assert result["source"] == "partial" and result["checked"] == 7000 and result["hits"] == 7
    data["info"]["partial"] = False
    result = selected(data)
    assert result["hasProgress"] is False and result["hits"] is None


@pytest.mark.parametrize("state,alive", [("paused", True), ("pause_requested", True),
                                         ("finished", False), ("restart_required", False)])
def test_paused_current_worker_keeps_counters_but_terminal_worker_does_not(state, alive):
    data = live_options()
    data["info"]["scan_control"] = {"supported": True, "owner_scan_key": "strategy_scan", "run_id": "run-1",
                                    "state": state, "worker_alive": alive, "scope": "strategy_round"}
    assert selected(data)["hasProgress"] is alive


def test_control_owner_mismatch_rejects_even_identical_run_ids():
    data = live_options()
    ctl = {"supported": True, "owner_scan_key": "strategy_scan", "run_id": "run-1",
           "state": "running", "worker_alive": True, "scope": "strategy_round"}
    data["info"]["scan_control"] = ctl
    data["schedulerState"]["control"] = {**ctl, "owner_scan_key": "strat_cup_and_handle_breakout", "scope": "scanner"}
    assert selected(data)["hasProgress"] is False


def test_matching_wrong_bi_owner_cannot_claim_the_selected_direction():
    data = live_options()
    data.update(scanKey="bi_long", strategy=None)
    ctl = {"supported": True, "owner_scan_key": "bi_short", "run_id": "run-1",
           "state": "running", "worker_alive": True, "scope": "scanner"}
    data["info"]["scan_control"] = ctl
    data["schedulerState"]["control"] = ctl
    assert selected(data)["hasProgress"] is False


def test_unsupported_crypto_pause_capability_does_not_suppress_current_partial_progress():
    data = live_options()
    data.update(scanKey="crypto_strat_test", strategy="Test")
    data["info"]["diagnostics"] = {"strategy": "Test"}
    ctl = {"supported": False, "owner_scan_key": "crypto_strat_test", "run_id": "run-1",
           "state": "finished", "worker_alive": True, "scope": "scanner"}
    data["info"]["scan_control"] = ctl
    data["schedulerState"]["control"] = ctl
    result = selected(data)
    assert result["hasProgress"] is True and result["source"] == "partial"
    assert result["checked"] == 7000 and result["hits"] == 7


RENDER = """
const assert=require('node:assert/strict');
const babel=require(BABEL);
const compiled=babel.transform(COMPONENT,{presets:['react'],sourceType:'script'}).code;
const React={useContext:()=>false,createElement:(type,props,...children)=>({type,props:props||{},children})};
const components=new Function('React','useState','useEffect','getScanTiming','formatCacheAge',
"const API='';const ScannerAdminContext={};const useRef=v=>({current:v});\\n"+PURE+compiled+'\\nreturn {ScanControl,LiveScanStatus};')(React,initial=>[initial,()=>{}],()=>{},()=>({}),()=> 'QA');
function text(n){return Array.isArray(n)?n.map(text).join(''):n==null||typeof n==='boolean'?'':typeof n==='object'?text(n.children):String(n);}
function nodes(n){return Array.isArray(n)?n.flatMap(nodes):!n||typeof n!=='object'?[]:[n,...nodes(n.children)];}
"""


def render(code):
    component = SOURCE[SOURCE.index("function ScanControl({"):SOURCE.index("function stockCompanyName(")]
    preamble = RENDER.replace("BABEL", json.dumps(str(ROOT / "frontend/vendor/babel.min.js")))
    preamble = preamble.replace("COMPONENT", json.dumps(component)).replace("PURE", json.dumps(PURE))
    node_run(preamble + code)


def test_both_shipped_surfaces_show_same_selected_runtime_progress_and_unknown_hits():
    progress = selected(options())
    render("""
const progress=PROGRESS;
const main=components.ScanControl({onScan:()=>{},isScanning:true,progressOverride:progress});
const lower=components.LiveScanStatus({active:true,partial:false,checked:12590,total:12590,count:7,progress});
assert.match(text(main),/7306 \\/ 12590 analysiert/);assert.match(text(main),/Trefferzahl noch nicht bestaetigt/);
assert.match(text(lower),/7306\\/12590 geprueft/);assert.match(text(lower),/7 Treffer aus gespeichertem Ergebnisstand/);
assert.ok(!/undefined|NaN|Scan wird vorbereitet/.test(text(main)+text(lower)));
assert.ok(nodes(main).some(n=>n.props.style?.width==='58%'));
assert.ok(nodes(lower).some(n=>n.props.style?.width==='58%'));
""".replace("PROGRESS", json.dumps(progress)))


@pytest.mark.parametrize("bad", [None, "4", -1, True])
def test_real_control_progress_never_renders_invalid_hits_or_percent(bad):
    render("""
const main=components.ScanControl({onScan:()=>{},isScanning:true,scanKey:'orb',schedulerStatus:{scans:{orb:{running:true,
progress:{checked:17,total:60,hits:HITS}}}}});
assert.match(text(main),/17 \\/ 60 analysiert/);assert.match(text(main),/Trefferzahl noch nicht bestaetigt/);
assert.ok(!/undefined|NaN/.test(text(main)));assert.ok(nodes(main).some(n=>n.props.style?.width==='28%'));
""".replace("HITS", json.dumps(bad)))


def test_unknown_selected_progress_does_not_fall_through_to_sibling_scheduler():
    render("""
const main=components.ScanControl({onScan:()=>{},isScanning:true,scanKey:'strategy_scan',
progressOverride:{selected:true,running:true},schedulerStatus:{scans:{strategy_scan:{running:true,progress:{checked:99,total:100,hits:40}}}}});
assert.ok(!/99|40 Treffer|99%/.test(text(main)));
""")


def test_live_status_does_not_coerce_null_checked_or_call_unknown_progress_preparation():
    render("""
const lower=components.LiveScanStatus({active:true,partial:true,checked:null,total:60,count:2});
assert.match(text(lower),/Fortschritt noch nicht bestaetigt/);
assert.ok(!/0\\/60|Scan wird vorbereitet/.test(text(lower)));
""")


def test_scanner_and_bi_callsites_consume_the_shared_selected_progress():
    scanner = SOURCE[SOURCE.index("function ScannerTab("):SOURCE.index("function BIScannerTab(")]
    bi = SOURCE[SOURCE.index("function BIScannerTab("):SOURCE.index("function BiotechTab(")]
    assert "progressOverride={selectedProgress}" in scanner and "progress={selectedProgress}" in scanner
    assert "progressOverride={selectedProgress}" in bi and "selectedProgress.checked" in bi
    assert "patternProgress?.checked ?? scanInfo?.checked" not in scanner
    assert "diagnostics.checked || 0" not in bi
