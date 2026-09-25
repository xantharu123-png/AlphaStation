"""Execute actual chart projection and compact components without network."""
import copy
import json
from pathlib import Path

import pytest

from test_frontend_scanner_lifecycle import node_run

ROOT = Path(__file__).parent
SOURCE = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
PURE = SOURCE[SOURCE.index("function isElliottScannerSelection("):SOURCE.index("function isWyckoffPattern(")]


def evaluate(expression):
    return json.loads(node_run(PURE + "\nconsole.log(JSON.stringify(" + expression + "));"))


def fixture():
    candles = [dict(time=f"2026-01-{i:02d}", open=100, high=110, low=90, close=101) for i in range(1, 16)]
    points = [dict(label=str(i), time=f"2026-01-{i * 2 + 1:02d}",
                   observed_at=f"2026-01-{i * 2 + 1:02d}T21:00:00Z",
                   confirmed_at=f"2026-01-{i * 2 + 2:02d}T21:00:00Z",
                   price=90 if i % 2 == 0 else 110, kind="low" if i % 2 == 0 else "high") for i in range(6)]
    pattern = dict(id="one", family="impulse", direction="LONG", pattern_status="geometry_only",
                   subdivision_status="unverified", trade_ready=False, mail_eligible=False,
                   signal_kind="pattern_context", points=points, waves=[], confirmed_at="2026-01-12T21:00:00Z")
    return dict(ticker="TEST", timeframe="1D", row=dict(ticker="TEST", scanner="Elliott Wave Muster",
                elliott=dict(model="causal_elliott_v1", status="ok", timeframe="1D", as_of="2026-01-15T22:00:00Z", patterns=[pattern])),
                chartData=dict(ticker="TEST", timeframe="1D", candles=candles))


def project(payload):
    return evaluate("projectElliottChartEvidence(" + json.dumps(payload) + ")")


def test_exact_dated_anchors_project_without_inventing_subwaves():
    value = project(fixture())
    assert value["status"] == "available"
    assert len(value["points"]) == 6
    assert len(value["lines"]) == 1
    assert [p["text"] for p in value["markers"]] == list("012345")


@pytest.mark.parametrize("section,key,value", [("row", "ticker", "OTHER"), ("chartData", "ticker", "OTHER"),
    ("chartData", "timeframe", "4H"), ("root", "timeframe", "4H"), ("root", "ticker", "OTHER")])
def test_ticker_and_timeframe_mismatch_never_draw_another_stocks_count(section, key, value):
    payload = fixture()
    (payload if section == "root" else payload[section])[key] = value
    assert project(payload)["points"] == []


@pytest.mark.parametrize("field,value", [("confirmed_at", "2026-02-01T21:00:00Z"), ("price", 89),
    ("price", "90"), ("price_field", "open"), ("observed_at", "2026-01-30T21:00:00Z")])
def test_unmatched_or_future_anchor_cannot_be_drawn(field, value):
    payload = fixture()
    payload["row"]["elliott"]["patterns"][0]["points"][0][field] = value
    assert project(payload)["points"] == []


def test_claimed_confirmation_requires_observed_subdivision_evidence():
    payload = fixture()
    payload["row"]["elliott"]["patterns"][0]["pattern_status"] = "confirmed"
    assert project(payload)["points"] == []


def test_unsorted_candles_do_not_create_reversed_chart_lines():
    payload = fixture()
    payload["chartData"]["candles"].reverse()
    assert project(payload)["points"] == []


def test_count_selection_is_explicit_not_silently_replaced():
    payload = fixture()
    payload["selectedId"] = "missing-count"
    assert project(payload)["points"] == []


@pytest.mark.parametrize("family", ["impulse", "zigzag", "regular_flat", "expanded_flat", "contracting_triangle"])
@pytest.mark.parametrize("subdivide", [False, True])
def test_real_core_report_matches_actual_chart_ohlc(family, subdivide):
    from test_elliott_waves import pattern_bars, report_for
    bars = pattern_bars(family, subdivide=subdivide)
    report = report_for(bars)
    payload = dict(ticker="TEST", timeframe="1D", showSubwaves=True,
        row=dict(ticker="TEST", scanner="Elliott Wave Muster", elliott=report),
        chartData=dict(ticker="TEST", timeframe="1D", candles=[dict(time=b["timestamp"][:10],
            open=b["open"], high=b["high"], low=b["low"], close=b["close"]) for b in bars]))
    payload["selectedId"] = next(p["id"] for p in report["patterns"] if p["family"] == family)
    value = project(payload)
    assert value["status"] == "available", value
    assert len(value["lines"]) > 1
    assert any("." in marker["text"] for marker in value["markers"])


def test_compact_result_component_has_no_trade_grade_or_synthetic_plan():
    # This fails while the integration is missing, and later executes the shipped JSX.
    assert "function ElliottScannerResults(" in SOURCE, "Dedicated pattern results are not integrated"
    component = SOURCE[SOURCE.index("function ElliottScannerResults("):SOURCE.index("function ElliottPatternEvidence(")]
    node_run("""
const assert=require('node:assert/strict');
const Babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const React={createElement:(type,props,...children)=>({type,props:props||{},children})};
const render=new Function('React',""" + json.dumps(PURE) + """+Babel.transform(""" + json.dumps(component) + """,{presets:['react']}).code+';return ElliottScannerResults;')(React);
function text(n){return Array.isArray(n)?n.map(text).join(' '):n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):text(n.children);}
const row=""" + json.dumps(fixture()["row"]) + """;
const tree=render({rows:[row],onSelect:()=>{},emptyText:'Kein Muster'}), content=text(tree);
assert.match(content,/TEST/);assert.match(content,/Impuls/);assert.match(content,/Unterwellen offen/);
for(const bad of ['Trade-Score','Grade','Entry','Stop','TP','Signal-Mail gesperrt'])assert.ok(!content.includes(bad),bad);
assert.match(text(render({rows:[],onSelect:()=>{},emptyText:'Kein Muster'})),/Kein Muster/);
""")


def test_empty_pattern_list_uses_actual_feed_state_instead_of_claiming_no_matches():
    from test_frontend_scanner_lifecycle import PURE as scanner_pure
    component = SOURCE[SOURCE.index("function ElliottScannerResults("):SOURCE.index("function ElliottPatternEvidence(")]
    node_run("""
const assert=require('node:assert/strict');
const Babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const React={createElement:(type,props,...children)=>({type,props:props||{},children})};
const render=new Function('React',""" + json.dumps(scanner_pure + PURE) + """+Babel.transform(""" + json.dumps(component) + """,{presets:['react']}).code+';return ElliottScannerResults;')(React);
function text(n){return Array.isArray(n)?n.map(text).join(' '):n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):text(n.children);}
const base={hasLoaded:true,info:{cached_at:'2026-09-25T20:14:00Z',diagnostics:{coverage:'complete'}}};
for(const state of [
 {...base,info:{...base.info,data_quality:{cache_status:'stale'}}},
 {...base,error:'Marktdaten unvollständig'},
 {...base,running:true},
 {hasLoaded:false}
]){
 const content=text(render({rows:[],onSelect:()=>{},evidence:state,emptyText:'Keine passenden Wellenmuster im letzten Scan.'}));
 assert.ok(!content.includes('Keine passenden Wellenmuster'),content);
 assert.ok(!content.includes('0 Aktien'),content);
}
const current=text(render({rows:[],onSelect:()=>{},evidence:base}));
assert.match(current,/Scan abgeschlossen/);assert.match(current,/Keine passenden Wellenmuster/);
""")


def test_wyckoff_is_opt_in_outside_its_scanner_and_closed_by_default():
    from test_frontend_wyckoff_evidence import fixture as wyckoff_fixture
    component = SOURCE[SOURCE.index("function WyckoffPatternEvidence("):SOURCE.index("// Cup scanner evidence:")]
    wyckoff_pure = SOURCE[SOURCE.index("function isWyckoffPattern("):SOURCE.index("function usePublicPlans(")]
    node_run("""
const assert=require('node:assert/strict');
const Babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const React={createElement:(type,props,...children)=>({type,props:props||{},children})};
const render=new Function('React',""" + json.dumps(wyckoff_pure) + """+Babel.transform(""" + json.dumps(component) + """,{presets:['react']}).code+';return WyckoffPatternEvidence;')(React);
const patterns=[""" + json.dumps(wyckoff_fixture()[0]) + """];
assert.equal(render({patterns,timeframe:'4H',scannerRow:{scanner:'Momentum Breakout Long'}}),null);
assert.equal(render({patterns,timeframe:'1D',scannerRow:{scanner:'Elliott Wave Muster'}}),null);
for(const props of [{enabled:true},{scannerRow:{scanner:'Wyckoff Accumulation'}}]) {
 const tree=render({patterns,timeframe:'1D',...props});
 assert.equal(tree.type,'details');assert.ok(!tree.props.open);assert.equal(tree.children[0].type,'summary');
 const summary=JSON.stringify(tree.children[0]);assert.ok(summary.length<220);assert.ok(!summary.includes('A1'));
}
""")


def test_completed_empty_pattern_scan_is_not_a_failed_signal_scan():
    from test_frontend_scanner_lifecycle import evaluate as scanner_evaluate
    result = scanner_evaluate('scannerCompactEvidence({kind:"pattern",count:0,hasLoaded:true,info:{cached_at:"2026-09-25T12:00:00Z",diagnostics:{coverage:"complete"}}})')
    assert result["text"] == "Scan abgeschlossen · Keine passenden Wellenmuster"


@pytest.mark.parametrize("metadata", [
    {"data_quality": {"cache_status": "stale"}},
    {"diagnostics": {"coverage": "complete", "warning": "elliott_cache_session_stale"}},
])
def test_session_rollover_cannot_be_presented_as_successful_no_pattern_scan(metadata):
    from test_frontend_scanner_lifecycle import evaluate as scanner_evaluate
    info = {"cached_at": "2026-09-25T20:14:00Z", "cache_age_seconds": 120,
            "diagnostics": {"coverage": "complete"}, **metadata}
    options = dict(kind="pattern", count=0, hasLoaded=True, info=info)
    result = scanner_evaluate("scannerCompactEvidence(" + json.dumps(options) + ")")
    assert result["tone"] == "stale"
    assert "neu scannen" in result["text"]
    assert "Keine passenden" not in result["text"]
    payload = dict(info, data=[], partial=False, scan_running=False)
    assert scanner_evaluate("scannerPollOutcome(" + json.dumps(payload) + ",null,true)") == "stale"
    # A new live worker and a genuine failure retain precedence over old-cache age.
    payload["scan_running"] = True
    assert scanner_evaluate("scannerPollOutcome(" + json.dumps(payload) + ",null,true)") == "running"
    payload["scan_error"] = "scan_data_invalid"
    assert scanner_evaluate("scannerPollOutcome(" + json.dumps(payload) + ",null,true)") == "error"


def test_rollover_during_poll_stops_without_zero_result_success_toast():
    from test_frontend_scanner_lifecycle import lifecycle
    lifecycle("""
      const stale={data_quality:{cache_status:'stale'},
        diagnostics:{coverage:'complete',warning:'elliott_cache_session_stale'}};
      queue.push({body:payload(t1,'OLD')});
      render(options({scopeKey:'elliott',cachePrefix:'elliott'})); await settle();
      queue.push({body:payload(t1,'OLD')},{body:{status:'started',run_id:'new'}});
      await feed.start(); await settle();
      queue.push({body:payload(t2,null,{...stale,scan_run_id:'new'})}); await timer();
      assert.equal(feed.isScanning,false); assert.equal(timers.size,0);
      assert.equal(feed.results.length,0); assert.equal(feed.error,null);
      assert.equal(toasts.length,0);
      assert.equal(scannerCompactEvidence({kind:'pattern',info:feed.info,count:0,hasLoaded:true}).tone,'stale');
      // A read failure must not resurrect OLD after the server withdrew it.
      queue.push({error:true}); await feed.refresh(); await settle();
      assert.equal(feed.results.length,0);
    """)


def test_incomplete_attempt_takes_precedence_over_obsolete_pattern_cache():
    from test_frontend_scanner_lifecycle import evaluate as scanner_evaluate
    payload = dict(cached_at="2026-09-25T20:14:00Z", data=[], partial=False, scan_running=False,
                   data_quality={"cache_status": "stale"},
                   diagnostics={"coverage": "complete", "attempt_diagnostics": {"coverage": "incomplete"}})
    assert scanner_evaluate("scannerPollOutcome(" + json.dumps(payload) + ",null,true)") == "error"
    options = dict(kind="pattern", count=0, hasLoaded=True, info=payload)
    result = scanner_evaluate("scannerCompactEvidence(" + json.dumps(options) + ")")
    assert result["tone"] == "warning"
    assert "unvollständig" in result["text"]


def test_elliott_mobile_sidebar_fits_client_width_not_scrollbar_viewport():
    assert 'className="detail-sidebar sidebar-wide elliott-sidebar"' in SOURCE
    assert '.detail-sidebar.elliott-sidebar { width: 100% !important; max-width: 100% !important; }' in SOURCE


def test_leaving_wyckoff_scanner_does_not_keep_its_automatic_overlay_on_other_rows():
    from test_frontend_wyckoff_evidence import evaluate as wyckoff_evaluate
    result = wyckoff_evaluate('patternOverlaysAfterSelection({patterns:true,wyckoffSwings:true,sr:true}, {scanner:"Wyckoff Accumulation"}, {scanner:"Momentum Breakout Long"})')
    assert result == {"patterns": False, "wyckoffSwings": False, "sr": True}
    assert wyckoff_evaluate('patternOverlaysAfterSelection({patterns:true}, {scanner:"Momentum Breakout Long"}, {scanner:"Cup and Handle Breakout"})') == {"patterns": True}
    assert wyckoff_evaluate('patternOverlaysAfterSelection({patterns:false}, {}, {scanner:"Wyckoff Distribution"})') == {"patterns": True}
    assert 'patternOverlaysAfterSelection(previous, previousRow, scannerData)' in SOURCE
