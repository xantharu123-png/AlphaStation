"""Zero direction counts cannot diagnose market regime or provider health."""
import json

from test_frontend_scanner_lifecycle import ROOT, SOURCE, node_run


def test_actual_combined_render_keeps_counts_without_claiming_missing_side_is_healthy():
    component = SOURCE[SOURCE.index("function CryptoTradeSignalsTab("):
                       SOURCE.index("// Short Scanner Tab")]
    node_run("""
const assert=require('node:assert/strict');
const babel=require(""" + json.dumps(str(ROOT / "frontend/vendor/babel.min.js")) + """);
const React={createElement:(type,props,...children)=>({type,props:props||{},children})};
const noop=()=>{};
const compiled=babel.transform(""" + json.dumps(component) + """,{presets:['react']}).code;
const render=new Function('React','useState','useRef','useEffect','useSortable',
 'ScanControl','ScannerVisibilitySummary','BreakoutRetestWarning',
 compiled+';return CryptoTradeSignalsTab;')(
 React,x=>[x,noop],x=>({current:x}),noop,items=>({sorted:items,toggleSort:noop,sortKey:'entry_score'}),
 noop,noop,noop);
function text(node){
 if(Array.isArray(node))return node.map(text).join(' ');
 if(node==null||typeof node==='boolean')return '';
 if(typeof node!=='object')return String(node);
 return text(node.children);
}
for(const direction of ['LONG','SHORT']){
 const row={Symbol:'TEST',direction,exchange:'binance',contract:'TESTUSDT',
   trade_action:direction==='LONG'?'LONG_ARMED':'SHORT_WATCH'};
 const stats={result_count:1,long_count:direction==='LONG'?1:0,
   short_count:direction==='SHORT'?1:0,trade_now_count:0,wait_count:1};
 const tree=render({onSelectTicker:noop,scanCache:{crypto_trade_signals_items:[row],
   crypto_trade_signals_stats:stats},schedulerStatus:{scans:{crypto_explosion:{
     last_error:'scan_data_unavailable'}}}});
 const visible=text(tree);
 assert.match(visible,/TEST/);assert.match(visible,/Long/);assert.match(visible,/Short/);
 assert.ok(visible.includes('Watch/Armed'));
 for(const unsupported of ['Das ist kein Fehler','sondern Marktlage','Risk-Off-Markt',
   'keiner aktiv','keiner im Monitoring'])assert.ok(!visible.includes(unsupported),unsupported);
}
""")


def test_admin_displays_crypto_consent_count_separately_from_swing_recipients():
    assert "['Crypto-Empfänger',mailAudit.delivery.recipient_counts?.crypto]" in SOURCE
    assert "['Swing-Empfänger',mailAudit.delivery.recipient_counts?.stocks_swing]" in SOURCE
