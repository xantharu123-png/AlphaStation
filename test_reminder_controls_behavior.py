"""Execute the shipped reminder UI/notification flow without network or SMTP."""
import json
import subprocess

from test_frontend_scanner_lifecycle import ROOT, SOURCE, node_run


def run_ui(code):
    source = SOURCE[SOURCE.index("function tradeReminderNotification("):SOURCE.index("// Custom Hooks")]
    preamble = r"""
const assert = require('node:assert/strict');
const babel = require(BABEL);
let cursor=0, states=[], effects=[], pending=[], dirty=false, entry=null, props={}, result;
const timers=new Map(), queue=[], calls=[], notices=[];
let timerId=0;
const useState=initial=>{const i=cursor++; if(!(i in states)) states[i]=typeof initial==='function'?initial():initial;
  return [states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;dirty=true;}];};
const useRef=initial=>{const i=cursor++;return states[i]||(states[i]={current:initial});};
const useEffect=(fn,deps)=>{const i=cursor++;const old=effects[i];
  if(!old||deps.some((d,j)=>!Object.is(d,old.deps[j]))) pending.push(()=>{
    old?.cleanup?.();effects[i]={deps,cleanup:fn()};});};
const React={createElement:(type,props,...children)=>({type,props:props||{},children})};
const storage=new Map();
const localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)};
const browserWindow={addToast:(...args)=>notices.push(args),addEventListener:()=>{},removeEventListener:()=>{}};
const fetch=async(url,init={})=>{calls.push({url,init});assert.ok(queue.length,'Unexpected request '+url);
  const item=queue.shift(); if(item.promise)return item.promise;
  return {ok:item.ok!==false,status:item.ok===false?503:200,json:async()=>item};};
const setInterval=fn=>{timers.set(++timerId,fn);return timerId;};
const clearInterval=id=>timers.delete(id);
const compiled=babel.transform(SOURCE,{presets:['react'],sourceType:'script'}).code;
const components=new Function('React','useState','useEffect','useRef','fetch','window','localStorage','setInterval','clearInterval',
  "const API='';\n"+compiled+"\nreturn {TradeReminderPoller, Controls:typeof TradeReminderControls==='function'?TradeReminderControls:null, Feed:typeof useActiveTradeReminders==='function'?useActiveTradeReminders:null};"
)(React,useState,useEffect,useRef,fetch,browserWindow,localStorage,setInterval,clearInterval);
function render(next=props){props=next;cursor=0;dirty=false;pending=[];result=entry(props);pending.forEach(fn=>fn());return result;}
async function settle(){for(let i=0;i<25;i++){await Promise.resolve();if(dirty)render();}}
function nodes(n){return Array.isArray(n)?n.flatMap(nodes):!n||typeof n!=='object'?[]:[n,...nodes(n.children)];}
function text(n){return Array.isArray(n)?n.map(text).join(''):n==null||typeof n==='boolean'?'':typeof n==='object'?text(n.children):String(n);}
function named(label,type){const n=nodes(result).find(n=>(!type||n.type===type)&&(n.props['aria-label']===label||text(n)===label));assert.ok(n,'Missing control: '+label);return n;}
function select(label,value){named(label,'select').props.onChange({target:{value}});render();}
function check(label,checked){named(label,'input').props.onChange({target:{checked}});render();}
function response(reminders){return {ok:true,json:async()=>({reminders})};}
"""
    preamble = preamble.replace("BABEL", json.dumps(str(ROOT / "frontend/vendor/babel.min.js")))
    preamble = preamble.replace("SOURCE", json.dumps(source))
    try:
        node_run(preamble + "\n(async()=>{\n" + code + "\n})().catch(e=>{console.error(e);process.exitCode=1});")
    except subprocess.CalledProcessError as exc:
        raise AssertionError(exc.stderr[-4000:]) from None


def test_duration_and_channels_are_independent_and_submit_exact_selection():
    run_ui("""
assert.equal(typeof components.Controls,'function','Reminder form is missing');
entry=components.Controls; const saved=[];
render({eligible:true,isStructure:true,condition:'retest',onCreate:(...v)=>saved.push(v)});
assert.deepEqual(nodes(named('Laufzeit','select')).filter(n=>n.type==='option').map(n=>Number(n.props.value)),[24,72,168,336,720]);
assert.equal(named('Mail','input').props.checked,true);assert.equal(named('App','input').props.checked,true);
select('Laufzeit','24');named('Reminder setzen','button').props.onClick();
select('Laufzeit','72');check('App',false);named('Reminder setzen','button').props.onClick();
check('Mail',false);check('App',true);named('Reminder setzen','button').props.onClick();
assert.deepEqual(saved,[[24,'email_browser'],[72,'email'],[72,'browser']]);
""")


def test_no_channel_cannot_create_reminder_even_if_click_handler_is_invoked():
    run_ui("""
assert.equal(typeof components.Controls,'function');entry=components.Controls;const saved=[];
render({eligible:true,isStructure:true,onCreate:(...v)=>saved.push(v)});
check('Mail',false);check('App',false);
assert.equal(named('Reminder setzen','button').props.disabled,true);
named('Reminder setzen','button').props.onClick();assert.equal(saved.length,0);
""")


def test_all_active_conditions_can_be_deleted_without_creating_new_reminders():
    run_ui("""
assert.equal(typeof components.Controls,'function');entry=components.Controls;const deleted=[];
render({eligible:false,onCancel:id=>deleted.push(id),activeReminders:[
  {id:'retest',condition:'retest',channel:'browser',expires_at:'2026-10-01T18:00:00Z'},
  {id:'breakout',condition:'trigger',channel:'email',expires_at:'2026-10-02T18:00:00Z'}]});
const buttons=nodes(result).filter(n=>n.type==='button'&&text(n)==='Löschen');
assert.equal(buttons.length,2);buttons.forEach(b=>b.props.onClick());
assert.deepEqual(deleted,['retest','breakout']);assert.ok(!text(result).includes('Reminder setzen'));
""")


def test_app_poller_never_shows_mail_only_reminders_or_replays_seen_events():
    run_ui("""
entry=components.TradeReminderPoller;
const reminders=[{id:'mail',ticker:'MAIL',channel:'email',status:'triggered'},
 {id:'app',ticker:'APP',channel:'browser',status:'triggered'},
 {id:'both',ticker:'BOTH',channel:'email_browser',status:'triggered'}];
queue.push({reminders});render();await settle();
assert.equal(notices.length,2,'Mail-only must not produce an app notification');
assert.ok(notices.every(n=>!n[0].includes('MAIL')));
queue.push({reminders});await [...timers.values()][0]();await settle();assert.equal(notices.length,2);
""")


def test_app_poller_does_not_replay_older_notifications_after_reload():
    run_ui("""
entry=components.TradeReminderPoller;
const reminders=Array.from({length:101},(_,i)=>({id:'reminder-'+i,ticker:'TEST',channel:'browser',status:'triggered'}));
queue.push({reminders});render();await settle();assert.equal(notices.length,101);
effects.forEach(effect=>effect?.cleanup?.());states=[];effects=[];
queue.push({reminders});render();await settle();
assert.equal(notices.length,101,'Reload must not replay a previously displayed reminder');
""")


def test_reminders_remain_deletable_in_every_sidebar_without_a_trade_plan():
    source = SOURCE[SOURCE.index("function DetailSidebar("):SOURCE.index("// Main App")]
    code = r"""
const assert=require('node:assert/strict');const babel=require(BABEL);
const compiled=babel.transform(SOURCE,{presets:['react'],sourceType:'script'}).code;
const Controls=()=>null;let cursor=0,current;
const env={React:{createElement:(type,props,...children)=>({type,props:props||{},children})},
 useState:initial=>{const i=cursor++;return [i===4?false:typeof initial==='function'?initial():initial,()=>{}];},
 useRef:current=>({current}),useEffect:()=>{},useMemo:fn=>fn(),window:{innerWidth:1440},API:'',
 useActiveTradeReminders:()=>({reminders:[{id:'active-1',ticker:'TEST',asset_type:current.isCrypto?'crypto':'stock',status:'active'}],error:'',refresh:()=>{}}),
 isCupScannerSelection:()=>false,isWyckoffScannerSelection:()=>false,projectCupChartEvidence:()=>null,
 breakoutRetestWarningEvidence:()=>null,scannerCandidatePresentation:()=>null,stockStructureReminderSource:()=>null,
 stockCompanyName:()=>'',vwapDisplayLabel:()=>'',cryptoExecutionReminderCapability:()=>({supported:false,reason:'Source no longer supported'}),
 TradeReminderControls:Controls,BreakoutRetestWarning:()=>null,CupPatternEvidence:()=>null,WyckoffPatternEvidence:()=>null};
const Sidebar=new Function(...Object.keys(env),compiled+'\nreturn DetailSidebar;')(...Object.values(env));
const nodes=n=>Array.isArray(n)?n.flatMap(nodes):!n||typeof n!=='object'?[]:[n,...nodes(n.children)];
for(const row of [{isCrypto:true},{isCrypto:true,trade_health:{}},{isCrypto:true,trade_setup:{direction:'LONG'}},
 {isCrypto:false},{isCrypto:false,trade_setup:{direction:'LONG'}}]){
 cursor=0;current=row;
 const tree=Sidebar({ticker:'TEST',scannerData:row,onClose:()=>{}});
 const controls=nodes(tree).filter(n=>n.type===Controls);
 assert.equal(controls.length,1,'Existing reminders need exactly one delete location: '+JSON.stringify(row));
 assert.equal(controls[0].props.activeReminders[0].id,'active-1');
}
""".replace("BABEL", json.dumps(str(ROOT / "frontend/vendor/babel.min.js")))
    code = code.replace("SOURCE", json.dumps(source))
    try:
        node_run(code)
    except subprocess.CalledProcessError as exc:
        raise AssertionError(exc.stderr[-4000:]) from None


def test_active_feed_refresh_removes_triggered_and_expired_without_reopening_sidebar():
    run_ui("""
assert.equal(typeof components.Feed,'function');entry=()=>components.Feed('TEST','stock');
const active={id:'one',ticker:'TEST',asset_type:'stock',status:'active',expires_at:'2099-01-01T00:00:00Z'};
queue.push({reminders:[active,{...active,id:'other',ticker:'OTHER'}]});render();await settle();
assert.deepEqual(result.reminders.map(r=>r.id),['one']);
queue.push({reminders:[{...active,status:'triggered'}]});await [...timers.values()][0]();await settle();
assert.equal(result.reminders.length,0);
queue.push({reminders:[{...active,expires_at:'2000-01-01T00:00:00Z'}]});await [...timers.values()][0]();await settle();
assert.equal(result.reminders.length,0);
""")


def test_active_feed_discards_late_response_after_ticker_switch_and_keeps_rows_on_error():
    run_ui("""
assert.equal(typeof components.Feed,'function');entry=p=>components.Feed(p.ticker,'stock');
let finish;queue.push({promise:new Promise(r=>finish=r)});render({ticker:'OLD'});await settle();
const current={id:'new',ticker:'NEW',asset_type:'stock',status:'active',expires_at:'2099-01-01T00:00:00Z'};
queue.push({reminders:[current]});render({ticker:'NEW'});await settle();
finish(response([{...current,id:'old',ticker:'OLD'}]));await settle();
assert.deepEqual(result.reminders.map(r=>r.id),['new']);
queue.push({ok:false});await [...timers.values()][0]();await settle();
assert.deepEqual(result.reminders.map(r=>r.id),['new']);assert.ok(result.error);
""")


def test_notification_response_is_ignored_after_logout_or_unmount():
    run_ui("""
entry=components.TradeReminderPoller;let finish;
queue.push({promise:new Promise(resolve=>finish=resolve)});render();await settle();
effects.forEach(effect=>effect?.cleanup?.());
finish(response([{id:'old-owner',ticker:'TEST',channel:'browser',status:'triggered'}]));await settle();
assert.equal(notices.length,0,'A previous login must not display a late private notification');
assert.equal(timers.size,0);
""")
