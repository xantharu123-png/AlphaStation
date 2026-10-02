"""Initial scanner GETs recover locally without a reload or a false scan success.

Only the shipped hook is executed. Fetch responses and clocks are synthetic;
no API import, provider call, scan start, or other external I/O is performed.
"""
import json
import subprocess

import pytest

from test_frontend_scanner_lifecycle import HARNESS, HOOK, node_run


RECOVERY_HARNESS = r"""
// Keep the existing lifecycle harness unchanged. This suite additionally models
// timeout durations and browser fetch rejecting when its signal is aborted.
const timeoutDetails = new Map();
const baseTimeout = global.setTimeout;
const baseClearTimeout = global.clearTimeout;
global.setTimeout = (fn, delay = 0) => {
  const id = baseTimeout(fn); timeoutDetails.set(id, delay); return id;
};
global.clearTimeout = id => {
  timeoutDetails.delete(id); baseClearTimeout(id);
};
const baseFetch = global.fetch;
global.fetch = (url, init = {}) => {
  const pending = baseFetch(url, init);
  if (!init.signal) return pending;
  return new Promise((resolve, reject) => {
    const abort = () => {
      const error = new Error('synthetic aborted fetch'); error.name = 'AbortError';
      reject(error);
    };
    if (init.signal.aborted) { abort(); return; }
    init.signal.addEventListener('abort', abort, {once:true});
    pending.then(value => {
      init.signal.removeEventListener('abort', abort); resolve(value);
    }, error => {
      init.signal.removeEventListener('abort', abort); reject(error);
    });
  });
};
async function recoveryTimer() {
  assert.ok(timers.size, 'Expected a bounded read deadline or recovery retry');
  const [id, fn] = [...timers.entries()].sort((left, right) =>
    (timeoutDetails.get(left[0]) || 0) - (timeoutDetails.get(right[0]) || 0))[0];
  const delay = timeoutDetails.get(id);
  timers.delete(id); timeoutDetails.delete(id); fn(); await settle();
  return delay;
}
function cleanupHook() {
  for (const effect of effects) effect?.cleanup?.();
}
function assertReadOnly() {
  assert.equal(requests.filter(request => request.init.method === 'POST').length, 0);
  assert.equal(toasts.length, 0);
}
"""


def recovery(code):
    try:
        node_run(HOOK + HARNESS + RECOVERY_HARNESS + "\n(async()=>{\n" + code
                 + "\n})().catch(error=>{console.error(error);process.exitCode=1});")
    except subprocess.CalledProcessError as error:
        raise AssertionError(error.stderr.strip()) from error


def test_transport_error_cannot_expose_private_text_by_public_message_prefix():
    recovery("""
      const streamed=response(200,{});
      streamed.json=async()=>{throw new TypeError('Anmeldung apiKey=SECRET');};
      queue.push({promise:Promise.resolve(streamed)});
      render(options()); await settle();
      assert.ok(feed.error); assert.ok(!feed.error.includes('SECRET'));
      assert.equal(feed.hasLoaded,false); assert.ok(timers.size);
      queue.push({body:payload(t2,'RECOVERED')}); await recoveryTimer();
      assert.equal(feed.results[0].ticker,'RECOVERED'); assert.equal(feed.error,null);
      assert.equal(timers.size,0); assertReadOnly();
    """)


def test_hanging_initial_get_has_a_deadline_and_recovers_without_a_reload():
    recovery("""
      const never=defer(); queue.push({promise:never.promise});
      render(options()); await settle();
      assert.equal(requests.length,1); assert.equal(feed.loading,true);
      assert.ok(timers.size,'An initial GET must not leave the loading view forever');
      const deadlines=[...timeoutDetails.values()];
      assert.ok(deadlines.every(delay=>delay>0 && delay<=30000));
      await recoveryTimer();
      assert.equal(requests[0].init.signal.aborted,true);
      assert.equal(feed.loading,false); assert.equal(feed.hasLoaded,false);
      assert.ok(feed.error); assert.equal(feed.results.length,0);
      queue.push({body:payload(t2,'RECOVERED')}); await recoveryTimer();
      assert.equal(requests.length,2); assert.equal(feed.results[0].ticker,'RECOVERED');
      assert.equal(feed.loading,false); assert.equal(feed.error,null);
      assert.equal(timers.size,0); assertReadOnly();
      // An ignored provider promise cannot overwrite the later confirmed read.
      never.resolve(response(200,payload(t1,'OBSOLETE'))); await settle();
      assert.equal(feed.results[0].ticker,'RECOVERED'); assert.equal(feed.info.cached_at,t2);
    """)


@pytest.mark.parametrize("first", [
    {"error": True},
    {"status": 503, "body": {"detail": "apiKey=SECRET"}},
])
def test_first_transient_failure_retries_without_scheduler_revision(first):
    recovery("""
      const state={last_run:t1,running:false,last_attempt_at:t1,run_id:'same'};
      queue.push(FIRST); render(options({schedulerState:state})); await settle();
      assert.equal(feed.hasLoaded,false); assert.equal(feed.results.length,0);
      assert.ok(feed.error); assert.ok(!feed.error.includes('SECRET'));
      assert.ok(timers.size,'A transient first-read failure needs a bounded retry');
      queue.push({body:payload(t2,'RECOVERED')}); await recoveryTimer();
      assert.deepEqual(opts.schedulerState,state);
      assert.equal(requests.length,2); assert.equal(feed.results[0].ticker,'RECOVERED');
      assert.equal(feed.hasLoaded,true); assert.equal(feed.error,null);
      assert.equal(timers.size,0); assertReadOnly();
    """.replace("FIRST", json.dumps(first)))


def test_passive_scheduler_updates_do_not_cancel_a_slow_initial_get():
    recovery("""
      const slow=defer(); queue.push({promise:slow.promise});
      render(options({schedulerState:null})); await settle();
      const state={last_run:t1,running:false,last_attempt_at:t1,run_id:'same'};
      for(let attempt=0;attempt<6;attempt++) {
        render({...opts,schedulerState:{...state,last_attempt_at:attempt?'2026-09-08T10:0'+attempt+':00':t1}});
        await settle();
      }
      assert.equal(requests.length,1,'Passive scheduler revisions must share the live read');
      assert.equal(requests[0].init.signal.aborted,false);
      // A coalesced reconcile after the protected read may confirm the latest
      // scheduler revision once, but must not restart every intermediate read.
      queue.push({body:payload(t2,'FINAL')});
      slow.resolve(response(200,payload(t2,'FINAL'))); await settle();
      if(timers.size && requests.length===1) {
        assert.ok([...timeoutDetails.values()].every(delay=>delay<=500));
        await recoveryTimer();
      }
      assert.equal(feed.results[0].ticker,'FINAL'); assert.equal(feed.loading,false);
      assert.ok(requests.length<=2); assert.equal(requests[0].init.signal.aborted,false);
      assert.equal(timers.size,0); assertReadOnly();
    """)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_or_permission_failures_are_terminal_without_retry_hammer(status):
    recovery("""
      queue.push({status:STATUS,body:{detail:'apiKey=SECRET'}});
      render(options()); await settle();
      assert.equal(requests.length,1); assert.equal(feed.loading,false);
      assert.equal(feed.hasLoaded,false); assert.ok(feed.error);
      assert.ok(!feed.error.includes('SECRET'));
      assert.equal(timers.size,0,'An authorization failure must not retry indefinitely');
      const state=scannerEvidenceState({info:feed.info,error:feed.error,
        loading:feed.loading,hasLoaded:feed.hasLoaded,count:0,kind:'bi'});
      assert.equal(state.tone,'error'); assertReadOnly();
    """.replace("STATUS", str(status)))


def test_transient_first_read_retries_have_bounded_backoff_without_zero_success():
    recovery("""
      queue.push({error:true}); render(options()); await settle();
      const delays=[];
      for(let retry=0;retry<10;retry++) {
        assert.ok(timers.size,'An open tab should recover without a reload');
        queue.push({error:true}); delays.push(await recoveryTimer());
      }
      assert.equal(requests.length,11);
      assert.ok(delays.every(delay=>delay>=1500 && delay<=30000));
      assert.ok(delays.every((delay,index)=>!index || delay>=delays[index-1]));
      assert.equal(delays.at(-1),30000,'Persistent outages need capped backoff');
      assert.equal(feed.loading,false); assert.equal(feed.hasLoaded,false);
      assert.equal(feed.results.length,0); assert.ok(feed.error);
      const state=scannerEvidenceState({info:feed.info,error:feed.error,
        loading:feed.loading,hasLoaded:feed.hasLoaded,count:0,kind:'bi'});
      assert.equal(state.tone,'error'); assertReadOnly();
      cleanupHook(); await settle(); assert.equal(timers.size,0);
    """)


def test_retry_after_is_not_bypassed_by_passive_scheduler_revisions():
    recovery("""
      queue.push({status:429,body:{retry_after_seconds:90,detail:'apiKey=SECRET'}});
      render(options()); await settle();
      assert.equal(requests.length,1); assert.equal(timers.size,1);
      assert.ok([...timeoutDetails.values()].every(delay=>delay>=90000));
      for(let attempt=0;attempt<6;attempt++) {
        render({...opts,schedulerState:{last_run:t1,running:false,run_id:'revision-'+attempt}});
        await settle();
      }
      assert.equal(requests.length,1,'Scheduler updates must respect Retry-After');
      assert.equal(timers.size,1);
      assert.ok([...timeoutDetails.values()].every(delay=>delay>=90000));
      queue.push({body:payload(t2,'RECOVERED')},{body:payload(t2,'RECOVERED')});
      await recoveryTimer();
      if(timers.size) {
        assert.ok([...timeoutDetails.values()].every(delay=>delay<=500));
        await recoveryTimer();
      }
      assert.ok(requests.length>=2 && requests.length<=3);
      assert.equal(feed.results[0].ticker,'RECOVERED'); assert.equal(feed.error,null);
      assert.equal(timers.size,0); assertReadOnly();
    """)


def test_retry_after_longer_than_a_day_is_not_shortened_to_an_earlier_request():
    recovery("""
      queue.push({status:429,body:{retry_after_seconds:90000}});
      render(options()); await settle();
      assert.equal(requests.length,1); assert.equal(timers.size,1);
      assert.ok([...timeoutDetails.values()].every(delay=>delay>=90000000));
      assert.equal(feed.loading,false); assert.ok(feed.error); assertReadOnly();
      cleanupHook(); await settle(); assert.equal(timers.size,0);
    """)


def test_retry_after_larger_than_browser_timer_range_is_terminal_not_wrapped():
    recovery("""
      queue.push({status:429,body:{retry_after_seconds:2147484}});
      render(options()); await settle();
      assert.equal(requests.length,1); assert.equal(timers.size,0);
      assert.equal(feed.loading,false); assert.equal(feed.hasLoaded,false);
      assert.ok(feed.error); assertReadOnly();
    """)


def test_hanging_json_body_is_bounded_by_the_same_initial_read_deadline():
    recovery("""
      const body=defer();
      queue.push({promise:Promise.resolve({ok:true,status:200,headers:{get:()=>null},
        json:()=>body.promise})});
      render(options()); await settle();
      assert.equal(feed.hasLoaded,false); assert.equal(feed.loading,true);
      await recoveryTimer();
      assert.equal(requests[0].init.signal.aborted,true);
      assert.equal(feed.loading,false); assert.ok(feed.error);
      queue.push({body:payload(t2,'RECOVERED')}); await recoveryTimer();
      assert.equal(feed.results[0].ticker,'RECOVERED'); assert.equal(feed.error,null);
      body.resolve(payload(t1,'LATE')); await settle();
      assert.equal(feed.results[0].ticker,'RECOVERED'); assert.equal(feed.info.cached_at,t2);
      assert.equal(timers.size,0); assertReadOnly();
    """)


def test_first_body_transport_failure_is_retryable_without_a_reload():
    recovery("""
      queue.push({promise:Promise.resolve({ok:true,status:200,headers:{get:()=>null},
        json:async()=>{throw new TypeError('synthetic body transport interrupted: SECRET');}})});
      render(options()); await settle();
      assert.equal(feed.hasLoaded,false); assert.equal(feed.loading,false);
      assert.ok(feed.error); assert.ok(!feed.error.includes('SECRET'));
      assert.ok(timers.size,'A body transport fault is not a terminal schema error');
      queue.push({body:payload(t2,'RECOVERED')}); await recoveryTimer();
      assert.equal(requests.length,2); assert.equal(feed.results[0].ticker,'RECOVERED');
      assert.equal(feed.error,null); assert.equal(timers.size,0); assertReadOnly();
    """)


def test_invalid_json_is_terminal_without_exposing_parser_or_provider_details():
    recovery("""
      queue.push({promise:Promise.resolve({ok:true,status:200,headers:{get:()=>null},
        json:async()=>{throw new SyntaxError('invalid JSON: SECRET');}})});
      render(options()); await settle();
      assert.equal(requests.length,1); assert.equal(feed.hasLoaded,false);
      assert.equal(feed.loading,false); assert.ok(feed.error);
      assert.ok(!feed.error.includes('SECRET'));
      assert.equal(timers.size,0); assertReadOnly();
    """)


def test_scope_change_cancels_a_pending_initial_read_and_its_deadline():
    recovery("""
      const slow=defer(); queue.push({promise:slow.promise});
      render(options()); await settle();
      queue.push({body:payload(t2,'SHORT')});
      render(options({scopeKey:'bi:short',resultsUrl:'/results?direction=short'}));
      await settle();
      assert.equal(requests[0].init.signal.aborted,true);
      assert.equal(requests.length,2); assert.equal(timers.size,0);
      slow.resolve(response(200,payload(t1,'LONG'))); await settle();
      assert.equal(feed.results[0].ticker,'SHORT'); assert.equal(feed.info.cached_at,t2);
      assert.equal(feed.error,null); assertReadOnly();
    """)


def test_scope_change_cancels_an_old_initial_failure_retry():
    recovery("""
      queue.push({error:true}); render(options()); await settle();
      assert.ok(timers.size,'Fixture must have a scheduled initial retry');
      queue.push({body:payload(t2,'SHORT')});
      render(options({scopeKey:'bi:short',resultsUrl:'/results?direction=short'}));
      await settle();
      assert.equal(requests.length,2); assert.equal(feed.results[0].ticker,'SHORT');
      assert.equal(timers.size,0); assert.equal(feed.error,null); assertReadOnly();
    """)


@pytest.mark.parametrize("first", [{"error": True}, {"deferred": True}])
def test_unmount_cancels_both_pending_deadline_and_initial_recovery(first):
    recovery("""
      const slow=defer();
      queue.push(DEFERRED?{promise:slow.promise}:{error:true});
      render(options()); await settle();
      assert.ok(timers.size,'Fixture must have a deadline or an initial retry');
      cleanupHook(); await settle();
      if(DEFERRED) assert.equal(requests[0].init.signal.aborted,true);
      assert.equal(timers.size,0); assert.equal(requests.length,1);
      slow.resolve(response(200,payload(t2,'LATE'))); await settle();
      assert.equal(feed.results.length,0); assertReadOnly();
    """.replace("DEFERRED", "true" if first.get("deferred") else "false"))


def test_recovery_keeps_requested_run_evidence_and_never_accepts_another_final():
    recovery("""
      queue.push({body:payload(t1,'KEPT')}); render(options()); await settle();
      queue.push({body:payload(t1,'KEPT')},{body:{status:'started',accepted:true,run_id:'ours'}});
      await feed.start(); await settle();
      const slow=defer(); queue.push({promise:slow.promise}); await recoveryTimer();
      const pollRequest=requests.at(-1);
      render({...opts,schedulerState:{last_run:t1,running:true,run_id:'ours'}}); await settle();
      assert.equal(pollRequest.init.signal.aborted,false);
      slow.resolve(response(200,payload(t2,'OTHER',{scan_run_id:'other'}))); await settle();
      assert.equal(feed.results[0].ticker,'KEPT'); assert.equal(feed.info.cached_at,t1);
      assert.equal(feed.isScanning,false); assert.ok(feed.error);
      assert.equal(toasts.length,0); assert.equal(timers.size,0);
      assert.equal(requests.filter(request=>request.init.method==='POST').length,1);
    """)
