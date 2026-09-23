"""Uniform scanner controls must not manufacture unsupported pause actions."""
import json

import pytest

from test_frontend_scanner_lifecycle import SOURCE, PURE, ROOT, node_run, evaluate


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("admin", [False, True])
def test_uncontrolled_scanners_keep_same_buttons_without_fake_pause(running, admin):
    component = SOURCE[SOURCE.index("function ScanControl({"):SOURCE.index("function LiveScanStatus(")]
    node_run("""
const assert=require('node:assert/strict');
const babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const component=babel.transform(""" + json.dumps(component) + """,{presets:['react'],sourceType:'script'}).code;
const React={useContext:()=>false,createElement:(type,props,...children)=>({type,props:props||{},children})};
const render=new Function('React','useState','useEffect','getScanTiming','formatCacheAge',
"const API='';const ScannerAdminContext={};const useRef=v=>({current:v});\\n" + """ + json.dumps(PURE) + """ + component + '\\nreturn ScanControl;')(
React,initial=>[initial,()=>{}],()=>{},()=>({}),()=> 'QA');
const started=[];
const tree=render({onScan:()=>started.push(true),isScanning:""" + json.dumps(running) + """,
canControl:""" + json.dumps(admin) + """,scanKey:'biotech'});
function nodes(n){return Array.isArray(n)?n.flatMap(nodes):!n||typeof n!=='object'?[]:[n,...nodes(n.children)];}
function text(n){return Array.isArray(n)?n.map(text).join(''):n==null||typeof n==='boolean'?'':typeof n==='object'?text(n.children):String(n);}
const all=nodes(tree),start=all.find(n=>n.props.className?.includes('scan-action-button'));
const stop=all.find(n=>n.props.className?.includes('scan-stop-button'));
assert.ok(start);assert.ok(stop);
assert.equal(text(stop),'Stop / Pause');assert.equal(stop.props.disabled,true);
assert.equal(start.props.disabled,""" + json.dumps(running) + """);
assert.equal(start.children[1].props.className,'scan-action-label');
stop.props.onClick();assert.equal(started.length,0);
if(""" + json.dumps(running) + """) {
 assert.ok(text(tree).includes(""" + json.dumps("Steuerung noch nicht bestaetigt" if admin else "Administrator") + """));
 assert.equal(all.some(n=>n.type==='button'&&text(n)==='Status neu laden'),""" + json.dumps(admin) + """);
} else assert.match(stop.props.title,/Kein aktiver Scan/);
""")


@pytest.mark.parametrize("component", [
    "ScannerTab", "BIScannerTab", "BiotechTab", "BTCDivergenzTab", "EarlyMoversTab",
    "CryptoTradeSignalsTab", "ShortScannerTab", "MoneyFlowTab", "SectorNarrativeTab",
    "CrashMonitorTab", "NewListingTab", "PennyStocksTab", "VolumeSpikesTab", "ORBScannerTab",
])
def test_every_scanner_view_uses_the_same_control_component(component):
    start = SOURCE.index(f"function {component}(")
    end = SOURCE.find("\nfunction ", start + 1)
    body = SOURCE[start:end if end >= 0 else None]
    assert "<ScanControl " in body
    if component == "ORBScannerTab":
        assert 'onScan={triggerScan}' in body and 'scanKey="orb"' in body
        assert 'onClick={triggerScan}' not in body
        assert "⏳ Scanning..." not in body


def test_running_old_result_is_clearly_distinguished_from_current_partial():
    old = evaluate("scannerEvidenceState({running:true,info:{cached_at:'2026-09-23T06:00:00Z',partial:false}})")
    live = evaluate("scannerEvidenceState({running:true,info:{cached_at:'2026-09-23T06:00:00Z',partial:true}})")
    assert "Altstand, nicht zum aktuellen Lauf" in old["text"]
    assert "vorlaeufiger Zwischenstand" in live["text"]
    assert "Gespeicherter Altstand" in SOURCE


def test_unknown_progress_is_bounded_and_respects_reduced_motion():
    assert '.scan-indeterminate-segment { width: 30%;' in SOURCE
    assert SOURCE.count('className="scan-indeterminate-segment"') == 2
    assert '@media (prefers-reduced-motion: reduce)' in SOURCE
