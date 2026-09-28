"""Successful result lists need a data date, not a second results dashboard."""
import json

from test_frontend_scanner_lifecycle import PURE, ROOT, SOURCE, node_run


def test_success_hides_redundant_counts_but_failures_stay_visible():
    component = SOURCE[SOURCE.index("function ScannerEvidence("):SOURCE.index("// Scanner Tab")]
    node_run("""
const assert=require('node:assert/strict');
const babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const React={createElement:(type,props,...children)=>({type,props:props||{},children})};
const source=babel.transform(""" + json.dumps(component) + """,{presets:['react'],sourceType:'script'}).code;
const render=new Function('React','getRelativeTime',""" + json.dumps(PURE) + """+source+';return ScannerEvidence;')(React,()=> 'vor 3 min');
function text(n){
 if(Array.isArray(n))return n.map(text).join(' ');
 if(n==null||typeof n==='boolean')return '';
 if(typeof n!=='object')return String(n);
 if(n.type==='details'&&!n.props.open)return text(n.children.filter(c=>c?.type==='summary'));
 return text(n.children);
}
const info={cached_at:'2026-09-28T12:00:00Z',checked:12589,diagnostics:{coverage:'complete',
 data_mode:'completed_daily_swing',analysis_as_of:'2026-09-25T16:00:00-04:00',universe_count:12589},
 data_quality:{visibility_counts:{total:17,released:1,candidate_warning:16,context:0,warning_count:16}}};
const feed={info,diagnostics:info.diagnostics,results:Array.from({length:17},()=>({})),hasLoaded:true,refresh:()=>{}};
const tree=render({feed}), visible=text(tree);
assert.ok(!visible.includes('17 Ergebnisse'),'No repeated results count above the table');
assert.ok(!visible.includes('freigegeben'),'No repeated release count above the table');
assert.ok(!tree.props.className.includes('bg-amber-50'),'Row-level warnings do not color the whole list');
assert.match(visible,/25.09.2026/);assert.match(visible,/Diagnose/);
const failure=render({feed:{...feed,error:'Marktdaten fehlen.',info:{...info,scan_error:'scan_data_invalid'}}});
assert.match(text(failure),/Marktdaten fehlen/);assert.match(text(failure),/Vorheriges Ergebnis/);
assert.match(text(failure),/Status erneut laden/);
""")


def test_other_scanners_do_not_repeat_counts_either():
    component = SOURCE[SOURCE.index("function ScannerVisibilitySummary("):SOURCE.index("function ScannerCandidateStatus(")]
    node_run("""
const assert=require('node:assert/strict');
const babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const React={createElement:(type,props,...children)=>({type,props:props||{},children})};
const render=new Function('React','scannerVisibleRowsSummary',babel.transform(""" + json.dumps(component) + """,{presets:['react']}).code+';return ScannerVisibilitySummary;')(React,rows=>rows);
assert.equal(render({rows:{released:2,candidate_warning:3,context:0,unclassified:0}}),null);
const unknown=render({rows:{released:1,candidate_warning:0,context:0,unclassified:2}});
assert.ok(unknown);assert.match(JSON.stringify(unknown),/Sichtbarkeitsnachweis/);
""")
