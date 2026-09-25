"""Compact scanner status keeps user-critical states outside closed diagnostics."""
import json

import pytest

from test_frontend_scanner_lifecycle import PURE, ROOT, SOURCE, evaluate, node_run


STAMP = "2026-09-25T12:52:52Z"


def compact(**overrides):
    options = dict(info={"cached_at": STAMP, "diagnostics": {"coverage": "complete"}},
                   hasLoaded=True, loading=False, running=False, error=None, count=0)
    options.update(overrides)
    return evaluate(f"scannerCompactEvidence({json.dumps(options)})")


@pytest.mark.parametrize("overrides,tone,fragment", [
    ({}, "empty", "Keine freigegebenen Signale"),
    ({"kind": "bi"}, "empty", "Keine freigegebenen BI-Signale"),
    ({"count": 2}, "ready", "2 Ergebnisse"),
    ({"kind": "bi", "count": 2}, "ready", "BI-Setups (17/20)"),
    ({"loading": True}, "loading", "werden geladen"),
    ({"info": None, "hasLoaded": False}, "missing", "Noch kein Scanergebnis"),
    ({"running": True}, "running", "vorherige Ergebnis"),
    ({"info": None, "running": True}, "running", "Scan läuft"),
    ({"info": {"partial": True}, "running": True, "count": 3}, "running", "Zwischenstand: 3"),
    ({"error": "Anmeldung abgelaufen. Bitte erneut anmelden."}, "error", "Anmeldung abgelaufen"),
    ({"info": {"scan_error": "scan_data_invalid"}}, "error", "Marktdaten fehlen"),
    ({"info": {"cached_at": STAMP, "warnings": ["Cache alt: 6h"]}}, "stale", "Ergebnis veraltet"),
    ({"info": {"cached_at": STAMP, "diagnostics": {"coverage": "incomplete"}}}, "warning", "Scan unvollständig"),
    ({"info": {"cached_at": STAMP}}, "warning", "noch nicht bestätigt"),
    ({"info": {"cached_at": STAMP, "diagnostics": {"coverage": "complete_with_exclusions"}}}, "warning", "Datenausschlüssen"),
    ({"kind": "bi", "info": {"cached_at": STAMP, "diagnostics": {"raw_cache_rows": 2, "validated_scanner_signals": 0}}}, "warning", "ohne gültigen 17/20"),
    ({"kind": "bi", "info": {"cached_at": STAMP, "diagnostics": {"validated_scanner_signals": 2, "decorated_scanner_signals": 0}}}, "warning", "Ausgabe blockiert"),
])
def test_compact_status_does_not_turn_failures_or_unverified_results_into_zero(overrides, tone, fragment):
    value = compact(**overrides)
    assert value["tone"] == tone
    assert fragment in value["text"]
    assert len(value["text"]) < 150


def test_candidate_warning_counts_are_visible_without_claiming_mail_approval():
    value = compact(count=3, info={"cached_at": STAMP, "data_quality": {
        "visibility_counts": {"total": 3, "released": 1, "candidate_warning": 1, "context": 1, "warning_count": 2}}})
    assert value["tone"] == "candidates"
    assert value["text"] == "3 Ergebnisse · 1 freigegeben · 1 mit Warnungen · 1 Marktkontext"


def test_blocked_start_has_priority_without_repeating_protocol_explanations():
    value = compact(error="Marktdaten fehlen.", blockedStart=(
        "Nicht gestartet: Biotech belegt den gemeinsamen Scanner. Es wurde kein neuer Lauf gestartet oder eingereiht."))
    assert value == {"tone": "warning", "text": "Nicht gestartet: Biotech belegt den gemeinsamen Scanner."}


@pytest.mark.parametrize("as_of,expected", [
    ("2026-09-24T16:00:00-04:00", "1D · Schlusskurse 24.09.2026"),
    (None, "1D · Schlusskurse"), ("invalid", "1D · Schlusskurse"),
])
def test_daily_data_label_does_not_suggest_live_quotes(as_of, expected):
    info = {"diagnostics": {"data_mode": "completed_daily_swing", "analysis_as_of": as_of}}
    assert evaluate(f"scannerCompactDataLabel({json.dumps(info)})") == expected
    assert evaluate("scannerCompactDataLabel({})") is None


def test_diagnostics_are_present_but_closed_and_critical_status_is_outside():
    component = SOURCE[SOURCE.index("function ScannerEvidence("):SOURCE.index("// Scanner Tab")]
    node_run("""
const assert=require('node:assert/strict');
const babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const source=babel.transform(""" + json.dumps(component) + """,{presets:['react'],sourceType:'script'}).code;
const React={createElement:(type,props,...children)=>({type,props:props||{},children})};
const render=new Function('React','getRelativeTime',""" + json.dumps(PURE) + """+source+';return ScannerEvidence;')(React,()=> 'vor 3 min');
function text(n,visible=true){
  if(Array.isArray(n))return n.map(v=>text(v,visible)).join(' ');
  if(n==null||typeof n==='boolean')return '';
  if(typeof n!=='object')return String(n);
  if(visible&&n.type==='details'&&!n.props.open)return text(n.children.filter(c=>c?.type==='summary'),visible);
  return text(n.children,visible);
}
function nodes(n){return Array.isArray(n)?n.flatMap(nodes):!n||typeof n!=='object'?[]:[n,...nodes(n.children)];}
const info={cached_at:'2026-09-25T12:52:52Z',checked:12610,total:12610,diagnostics:{
 coverage:'complete',data_mode:'completed_daily_swing',analysis_as_of:'2026-09-24T16:00:00-04:00',
 universe_count:12610,checked:12610,total:12610,raw_matches_before_special_filter:121,
 visible_results_after_signal_policy:0,rejected:{change_filter:1294}}};
const feed={info,diagnostics:info.diagnostics,results:[],hasLoaded:true,refresh:()=>{}};
const tree=render({feed}), visible=text(tree), full=text(tree,false);
assert.match(visible,/Keine freigegebenen Signale/);assert.match(visible,/1D · Schlusskurse 24.09.2026/);
assert.match(visible,/Details/);assert.ok(visible.length<130);
for(const value of ['Pruefprotokoll','12610','1294','garantierte','Vor Freigabe']) {
 assert.ok(!visible.includes(value),value+' should be collapsed');assert.ok(full.includes(value),value+' must remain available');
}
const details=nodes(tree).find(n=>n.props['data-testid']==='scanner-diagnostics');
assert.equal(details.type,'details');assert.ok(!details.props.open);
const error=render({feed:{...feed,error:'Marktdaten fehlen.',info:{...info,scan_error:'scan_data_invalid'}}});
assert.match(text(error),/Marktdaten fehlen/);assert.match(text(error),/Vorheriges Ergebnis/);
assert.match(text(error),/Status erneut laden/);assert.ok(!text(error).includes('Keine freigegebenen Signale'));
const busy=render({feed:{...feed,error:'Marktdaten fehlen.',blockedStart:'Nicht gestartet: Biotech belegt den gemeinsamen Scanner. Kein neuer Lauf.'}});
assert.match(text(busy),/Nicht gestartet: Biotech/);assert.ok(!text(busy).includes('Marktdaten fehlen'));
assert.ok(text(busy,false).includes('Marktdaten fehlen'));
""")


def test_strategy_and_bi_empty_lists_use_the_same_compact_status():
    assert SOURCE.count('scannerCompactEvidence({ info: scanInfo,') == 2
    assert 'scannerCompactEvidence({ info: liveInfo,' in SOURCE
