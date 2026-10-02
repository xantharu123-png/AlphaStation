"""Execute the actual shipped backtest controller without network or React mocks."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent

DRIVER = r"""
const assert = require('node:assert/strict');
const API = 'http://synthetic.invalid';
const storage = new Map();
const window = {localStorage: {getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value)}};
const request = {ticker:'AAPL', strategy:'sma_crossover', months:3,
  max_tickers:50, min_price:0, min_volume:0};
const report = (req=request) => ({request:{...req}, total_trades:1,
  trades:[{ticker:req.ticker, pnl_pct:0.004}], cache_saved:true,
  cached_at:'2026-10-02T09:00:00Z'});
const envelope = (req=request) => ({status:'success', data:report(req),
  request:{...req}, cached_at:'2026-10-02T09:00:00Z'});
const response = (data, status=200) => ({ok:status>=200 && status<300,
  status, json:async()=>data});
const calls=[], states=[];
const publish = state => states.push(state);
const last = () => states.at(-1);
const tick = () => new Promise(resolve=>setImmediate(resolve));
"""

CASES = {
    "server_and_ui_caps_do_not_change_or_hide_total": r"""
const counts=backtestTradeCounts({trades:new Array(800).fill({}),trades_total:1000});
assert.deepEqual(counts,{received:800,total:1000,shown:30,notLoaded:200,hidden:970});
assert.equal(backtestTradeCounts({trades:[{}],trades_total:false}).total,1);
assert.equal(backtestTradeCounts({trades:[{},{}],trades_total:1}).total,2);
""",
    "passive_exact_get_no_post": r"""
global.fetch=async(url,opts)=>{calls.push({url,opts});return response(envelope());};
const session=createBacktestSession(publish);await session.select(request);
assert.equal(last().record.data.total_trades,1);
assert.equal(calls.length,1);
const query=new URL(calls[0].url).searchParams;
for(const [key,value] of Object.entries(request))assert.equal(query.get(key),String(value));
assert.ok(!calls[0].opts.method);session.dispose();
""",
    "reopen_reads_server_again_without_post": r"""
global.fetch=async(url,opts)=>{calls.push({url,opts});return response(envelope());};
let session=createBacktestSession(publish);await session.select(request);session.dispose();
session=createBacktestSession(publish);await session.select(request);session.dispose();
assert.equal(calls.length,2);assert.ok(calls.every(call=>!call.opts.method));
""",
    "universe_is_not_silently_aapl": r"""
const req={...request,ticker:'',strategy:'bi_short'};
global.fetch=async(url)=>{calls.push(url);return response(envelope(req));};
const session=createBacktestSession(publish);await session.select(req);
assert.equal(new URL(calls[0]).searchParams.get('ticker'),'');
assert.equal(last().record.request.ticker,'');session.dispose();
""",
    "missing_cache_is_not_zero_trade_report": r"""
global.fetch=async()=>response({status:'success',cache_status:'missing',
  data:{data_available:false},request});
const session=createBacktestSession(publish);await session.select(request);
assert.equal(last().record,null);assert.equal(last().cacheStatus,'missing');session.dispose();
""",
    "completed_zero_trade_report_is_restored": r"""
const body=envelope();body.data={...body.data,total_trades:0,trades:[],data_available:false};
global.fetch=async()=>response(body);
const session=createBacktestSession(publish);await session.select(request);
assert.equal(last().record.data.total_trades,0);assert.equal(last().cacheStatus,'saved');session.dispose();
""",
    "other_months_are_not_relabelled": r"""
global.fetch=async()=>response(envelope({...request,months:6}));
const session=createBacktestSession(publish);await session.select(request);
assert.equal(last().record,null);assert.match(last().error,/anderen Auswahl/);session.dispose();
""",
    "failed_refresh_retains_exact_saved_success": r"""
let count=0;global.fetch=async()=>++count===1?response(envelope()):
  response({detail:{code:'storage_unavailable',private_path:'not_to_render'}},503);
const session=createBacktestSession(publish);await session.select(request);
await session.select(request,true);assert.equal(last().record.data.total_trades,1);
assert.equal(typeof last().error,'string');assert.ok(!last().error.includes('not_to_render'));session.dispose();
""",
    "failed_post_retains_success_and_safe_structured_error": r"""
global.fetch=async(url,opts)=>opts.method==='POST'?
  response({detail:[{type:'greater_than',loc:['body','months']}]},422):
  response(url.includes('progress')?{percent:12}:envelope());
const session=createBacktestSession(publish);await session.select(request);
assert.equal(await session.run(request),false);
assert.equal(last().record.data.total_trades,1);assert.match(last().error,/prüfen/);
assert.equal(last().isRunning,false);session.dispose();
""",
    "synchronous_double_start_is_deduplicated": r"""
let resolvePost,posts=0;
global.fetch=async(url,opts)=>{if(opts.method==='POST'){posts++;
  return new Promise(resolve=>{resolvePost=resolve;});}return response({percent:12});};
const session=createBacktestSession(publish);const first=session.run(request);
assert.equal(await session.run(request),false);assert.equal(posts,1);
resolvePost(response(report()));assert.equal(await first,true);
assert.equal(last().isRunning,false);session.dispose();
""",
    "hanging_progress_cannot_hold_completed_result": r"""
global.fetch=async(url,opts)=>opts.method==='POST'?response(report()):new Promise(()=>{});
const session=createBacktestSession(publish);assert.equal(await session.run(request),true);
assert.equal(last().isRunning,false);assert.equal(last().record.data.total_trades,1);session.dispose();
""",
    "stale_request_after_selection_change_is_ignored": r"""
const pending=[];
global.fetch=(url)=>new Promise(resolve=>pending.push({url,resolve}));
const session=createBacktestSession(publish);const first=session.select(request);
const next={...request,months:6};const second=session.select(next);
pending[1].resolve(response(envelope(next)));await second;
pending[0].resolve(response(envelope(request)));await first;await tick();
assert.equal(last().record.request.months,6);session.dispose();
""",
    "unmount_suppresses_late_view_updates": r"""
let resolve;global.fetch=()=>new Promise(done=>{resolve=done;});
const session=createBacktestSession(publish);const read=session.select(request);
session.dispose();const before=states.length;
resolve(response(envelope()));await read;await tick();assert.equal(states.length,before);
""",
    "json_body_is_part_of_deadline": r"""
global.fetch=async()=>({ok:true,status:200,json:()=>new Promise(()=>{})});
await assert.rejects(readBacktestJson(API,{},null,15),
  error=>error.detail==='backtest_read_timeout');
""",
    "invalid_progress_cannot_crash_result_view": r"""
global.fetch=async(url,opts)=>opts.method==='POST'?response(report()):response(null);
const session=createBacktestSession(publish);assert.equal(await session.run(request),true);
assert.equal(last().progress.status,'done');session.dispose();
""",
    "fresh_unsaved_result_not_labelled_saved": r"""
global.fetch=async(url,opts)=>opts.method==='POST'?response({...report(),cache_saved:false,cached_at:null}):
  response({percent:12});
const session=createBacktestSession(publish);assert.equal(await session.run(request),true);
assert.equal(last().record.saved,false);assert.equal(last().record.cachedAt,null);session.dispose();
""",
    "preferences_only_no_result_or_token": r"""
storage.set('alpha_backtest_selection_v2',JSON.stringify({ticker:'NVDA',months:'3',
  results:{private:'report'},token:'private_token',strategy:42,minPrice:false,maxTickers:'100'}));
const prefs=loadBacktestPreferences();assert.equal(prefs.ticker,'NVDA');
assert.equal(prefs.strategy,'sma_crossover');assert.equal(prefs.minPrice,'');
assert.ok(!('results' in prefs));assert.ok(!('token' in prefs));
storage.set('alpha_backtest_selection_v2','invalid-json');
assert.equal(loadBacktestPreferences().months,'6');
""",
    "selection_default_zero_caps_and_bad_numbers": r"""
const form={ticker:' nvda ',months:'3',maxTickers:'50',minPrice:'',minVolume:''};
const option={id:'crypto_long',requires_ticker:false,default_min_price:5,
  default_min_volume:100,max_months:12,max_tickers_limit:120};
assert.equal(backtestSelection(form,option).request.min_price,5);
assert.equal(backtestSelection({...form,minPrice:'0'},option).request.min_price,0);
assert.equal(backtestSelection({...form,months:'24'},option).request,null);
assert.equal(backtestSelection({...form,maxTickers:'121'},option).request,null);
assert.equal(backtestSelection({...form,minPrice:'NaN'},option).request,null);
assert.equal(backtestSelection({...form,minVolume:'100000.5'},option).request,null);
assert.equal(backtestSelection(form,{...option,available:false}).request,null);
assert.equal(backtestSelection(form,{id:'sma_crossover'}).request.ticker,'NVDA');
""",
}


@pytest.mark.parametrize("case", CASES)
def test_shipped_backtest_session(case):
    node = shutil.which("node")
    assert node, "Node required to execute the shipped frontend"
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    begin = html.index("function loadBacktestPreferences()")
    end = html.index("function BacktestTab()", begin)
    script = DRIVER + html[begin:end] + "\n(async()=>{\n" + CASES[case] + r"""
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script], capture_output=True,
                            encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr


def test_missing_holdout_and_grade_metrics_are_not_invented_zero():
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    begin = source.index("function BacktestTab()")
    end = source.index("// Strategie Guide Tab", begin)
    component = source[begin:end]
    assert "outOfSample.holdout.max_drawdown ?? 0" not in component
    assert "stat.win_rate ?? 0" not in component
    assert "stat.profit_factor ?? 0" not in component
    assert "trade_sequence_compounded_return_pct" in component
    assert "Modell-Drawdown (Tradefolge)" in component
    assert "{results && !error &&" not in component
