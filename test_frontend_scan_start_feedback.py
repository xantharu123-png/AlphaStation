"""A rejected/unconfirmed manual start must survive unrelated result reads.

Execute the shipped hook AND ScanControl's actual start wrapper. Only fetch and
the React scheduling boundary are synthetic; no app import, network or mail.
"""
import json
import subprocess

import pytest

from test_frontend_scanner_lifecycle import HARNESS, HOOK, SOURCE, node_run


WRAPPER_START = SOURCE.index("    const startScan = async () => {", SOURCE.index("function ScanControl("))
WRAPPER_END = SOURCE.index("    // Auto-derive", WRAPPER_START)
CLICK_WRAPPER = SOURCE[WRAPPER_START:WRAPPER_END]
CLICK = """
function clickStart() {
    // Capture the props supplied by this render, as the real button does.
    const onScan = feed.start, controlFeed = feed;
    let starting = false;
    const setStarting = value => { starting = value; };
""" + CLICK_WRAPPER + "\n    return startScan();\n}\n"
SETUP = """
const old = payload(t1, 'KEPT', {scan_run_id:'old-run', scan_last_attempt_at:t1});
queue.push({body:old}); render(options()); await settle();
"""


def interaction(code, setup=SETUP):
    try:
        node_run(HOOK + HARNESS + CLICK + "\n(async()=>{\n" + setup + code
                 + "\n})().catch(e=>{console.error(e);process.exitCode=1});")
    except subprocess.CalledProcessError as failure:
        raise AssertionError(failure.stderr) from None


@pytest.mark.parametrize("post,fragment", [
    ({"status": 429, "body": {"retry_after_seconds": 90}}, "90 Sekunden"),
    ({"status": 401, "body": {}}, "Anmeldung"),
    ({"status": 500, "body": {}}, "HTTP 500"),
    ({"error": True}, "den Auftrag dennoch erhalten"),
    ({"body": {"status": "unexpected"}}, "nicht bestaetigt"),
    ({"body": None}, "nicht bestaetigt"),
])
def test_start_failure_survives_button_finally_and_same_cache_passive_reads(post, fragment):
    # Removing start-failure ownership lets the successful old GET erase it.
    interaction("""
      queue.push({body:old}, POST_REPLY, {body:old});
      await clickStart(); await settle();
      assert.ok(feed.error?.includes(FRAGMENT), 'Button refresh erased the start failure');
      const failure = feed.error;
      assert.equal(feed.isScanning,false); assert.equal(feed.results[0].ticker,'KEPT');
      assert.equal(feed.info.cached_at,t1); assert.equal(feed.notice,null);
      assert.equal(feed.blockedStart,null); assert.equal(toasts.length,0);
      assert.equal(requests.filter(r=>r.init.method==='POST').length,1);
      queue.push({body:old}); await feed.refresh(); await settle();
      assert.equal(feed.error,failure, 'Manual result refresh erased the start failure');
      queue.push({body:old});
      render({...opts,schedulerState:{last_run:t1,running:false,last_attempt_at:t2,run_id:'status-tick'}});
      await settle();
      assert.equal(feed.error,failure, 'Passive scheduler refresh erased the start failure');
      assert.equal(feed.isScanning,false); assert.equal(timers.size,0);
      assert.equal(toasts.length,0);
    """.replace("POST_REPLY", json.dumps(post)).replace("FRAGMENT", json.dumps(fragment)))


@pytest.mark.parametrize("baseline,fragment", [
    ({"status": 503, "body": {}}, "HTTP 503"),
    ({"error": True}, "Verbindung"),
    ({"body": {"data": [None]}}, "Ungueltige Scanner-Antwort"),
])
def test_failed_baseline_remains_visible_after_wrapper_refresh_without_sending_post(baseline, fragment):
    # A successful cache read is not proof that a refused baseline launched.
    interaction("""
      queue.push(BASELINE, {body:old});
      await clickStart(); await settle();
      assert.ok(feed.error?.includes(FRAGMENT), 'Old cache hid the baseline failure');
      const failure=feed.error;
      assert.equal(requests.filter(r=>r.init.method==='POST').length,0);
      assert.equal(feed.isScanning,false); assert.equal(feed.results[0].ticker,'KEPT');
      queue.push({body:old}); await feed.refresh(); await settle();
      assert.equal(feed.error,failure); assert.equal(toasts.length,0);
    """.replace("BASELINE", json.dumps(baseline)).replace("FRAGMENT", json.dumps(fragment)))


def test_first_ever_failed_start_cannot_be_cleared_by_first_successful_old_cache_read():
    # No known baseline is not permission to treat an arbitrary old run as new.
    interaction("""
      queue.push({status:401,body:{}}); render(options()); await settle();
      assert.equal(feed.hasLoaded,false); assert.equal(feed.info,null);
      queue.push({status:503,body:{}},
        {body:payload(t1,null,{scan_run_id:'historical',scan_last_attempt_at:t1})});
      await clickStart(); await settle();
      assert.match(feed.error,/HTTP 503/, 'Historical first cache hid the failed start');
      assert.equal(feed.hasLoaded,true); assert.equal(feed.info.cached_at,t1);
      assert.equal(feed.isScanning,false); assert.equal(toasts.length,0);
      assert.equal(requests.filter(r=>r.init.method==='POST').length,0);
    """, setup="")


def test_timestamp_less_baseline_does_not_let_historical_cache_erase_post_failure():
    # A readable but unbound baseline must not make every dated cache "new".
    interaction("""
      const unknown=payload(null,null,{scan_run_id:null,scan_last_attempt_at:null});
      queue.push({body:unknown}); render(options()); await settle();
      queue.push({body:unknown},{status:500,body:{}},
        {body:payload(t1,'KEPT',{scan_run_id:'historical',scan_last_attempt_at:t1})});
      await clickStart(); await settle();
      assert.match(feed.error,/HTTP 500/); assert.equal(feed.info.cached_at,t1);
      assert.equal(feed.isScanning,false); assert.equal(toasts.length,0);
      assert.equal(requests.filter(r=>r.init.method==='POST').length,1);
    """, setup="")


def test_run_started_during_uncertain_post_supersedes_failure_without_prior_timestamp():
    # The intent, not the later network failure, bounds an unobserved baseline.
    interaction("""
      let clock=Date.parse(t1+'Z'); Date.now=()=>clock;
      const unknown=payload(null,null,{scan_run_id:null,scan_last_attempt_at:null});
      queue.push({body:unknown}); render(options()); await settle();
      let rejectPost; const pendingPost=new Promise((_,reject)=>{rejectPost=reject;});
      queue.push({body:unknown},{promise:pendingPost},
        {body:payload(t1,null,{scan_run_id:'current-run',scan_last_attempt_at:'2026-09-08T10:01:00',scan_running:true})});
      const pending=clickStart(); await settle();
      clock=Date.parse(t2+'Z'); rejectPost(new TypeError('network unavailable'));
      await pending; await settle();
      assert.equal(feed.error,null, 'A run started after intent was hidden by the later transport failure');
      assert.equal(feed.isScanning,true); assert.equal(feed.info.scan_run_id,'current-run');
      assert.equal(feed.notice,null); assert.equal(toasts.length,0);
    """, setup="")


def test_new_deliberate_attempt_clears_previous_start_failure_before_baseline_finishes():
    # Missing deliberate-reset state would keep an obsolete refusal on a retry.
    interaction("""
      queue.push({body:old},{status:429,body:{retry_after_seconds:90}},{body:old});
      await clickStart(); await settle(); assert.ok(feed.error);
      const baseline=defer(); queue.push({promise:baseline.promise},
        {body:{status:'started',accepted:true,run_id:'requested-new'}});
      const next=clickStart(); await settle();
      assert.equal(feed.error,null); assert.equal(feed.isScanning,true);
      baseline.resolve(response(200,old)); await next; await settle();
      assert.equal(feed.error,null); assert.equal(feed.isScanning,true);
      assert.match(feed.notice,/Scan angenommen/);
      assert.equal(requests.filter(r=>r.init.method==='POST').length,2);
    """)


@pytest.mark.parametrize("observed", [
    {"scan_run_id": "old-run", "scan_last_attempt_at": "2026-09-08T10:05:00"},
    {"scan_run_id": "new-run", "scan_last_attempt_at": "2026-09-08T10:00:00"},
    {"scan_run_id": "older-run", "scan_last_attempt_at": "2026-09-08T09:00:00"},
    {"scan_run_id": "new-run", "scan_last_attempt_at": "broken"},
    {"scan_run_id": "apiKey=SECRET", "scan_last_attempt_at": "2026-09-08T10:05:00"},
])
def test_same_or_unproven_run_cannot_clear_failed_start(observed):
    # A changed ID or a changed timestamp alone must not fabricate a later run.
    interaction("""
      queue.push({body:old},{status:429,body:{retry_after_seconds:90}},{body:old});
      await clickStart(); await settle(); const failure=feed.error; assert.ok(failure);
      queue.push({body:payload(t1,'KEPT',OBSERVED)}); await feed.refresh(); await settle();
      assert.equal(feed.error,failure); assert.equal(feed.isScanning,false);
      assert.equal(toasts.length,0);
    """.replace("OBSERVED", json.dumps(observed)))


@pytest.mark.parametrize("running", [False, True])
def test_proven_newer_different_run_supersedes_failure_without_manual_success_toast(running):
    # Never clearing owned failure would incorrectly hide fresh worker evidence.
    interaction("""
      queue.push({body:old},{status:429,body:{retry_after_seconds:90}},{body:old});
      await clickStart(); await settle(); assert.ok(feed.error);
      queue.push({body:payload(t2,'NEW',{scan_run_id:'new-run',scan_last_attempt_at:t2,
        scan_running:RUNNING,partial:RUNNING})});
      await feed.refresh(); await settle();
      assert.equal(feed.error,null); assert.equal(feed.info.scan_run_id,'new-run');
      assert.equal(feed.isScanning,RUNNING); assert.equal(toasts.length,0);
    """.replace("RUNNING", json.dumps(running)))


def test_new_final_timestamp_with_same_run_id_does_not_erase_rejected_attempt():
    # Completion of the already-observed old run is not the refused new start.
    interaction("""
      queue.push({body:old},{status:401,body:{}},{body:old});
      await clickStart(); await settle(); const failure=feed.error; assert.ok(failure);
      queue.push({body:payload(t2,'OLD-RUN-FINAL',{scan_run_id:'old-run',scan_last_attempt_at:t1})});
      await feed.refresh(); await settle();
      assert.equal(feed.error,failure); assert.equal(feed.info.cached_at,t2);
      assert.equal(toasts.length,0);
    """)


@pytest.mark.parametrize("stamp,extra", [
    ("2026-09-08T10:00:00", {"scan_last_attempt_at": "2099-01-01T00:00:00Z"}),
    ("2099-01-01T00:00:00Z", {"scan_last_attempt_at": None}),
    ("2026-09-08T10:05:00", {"scan_last_attempt_at": None, "diagnostics": {}}),
])
def test_future_or_unverified_result_is_not_proof_that_start_failure_was_superseded(stamp, extra):
    # Accepting any parseable timestamp would erase errors on invalid evidence.
    interaction("""
      queue.push({body:old},{status:500,body:{}},{body:old});
      await clickStart(); await settle(); const failure=feed.error; assert.ok(failure);
      queue.push({body:payload(STAMP,null,{scan_run_id:'new-run',...EXTRA})});
      await feed.refresh(); await settle();
      assert.equal(feed.error,failure, 'Unverified newer-run evidence erased the start failure');
      assert.equal(toasts.length,0);
    """.replace("STAMP", json.dumps(stamp)).replace("EXTRA", json.dumps(extra)))


def test_verified_new_final_run_can_supersede_failure_without_attempt_timestamp():
    # A producer's validated new final cache can prove a new run on its own.
    interaction("""
      queue.push({body:old},{status:500,body:{}},{body:old});
      await clickStart(); await settle(); assert.ok(feed.error);
      queue.push({body:payload(t2,null,{scan_run_id:'new-run',scan_last_attempt_at:null})});
      await feed.refresh(); await settle();
      assert.equal(feed.error,null); assert.equal(feed.info.cached_at,t2);
      assert.equal(feed.results.length,0); assert.equal(toasts.length,0);
    """)


def test_invalid_baseline_cache_timestamp_does_not_hide_comparable_later_final_run():
    # A valid old attempt still provides a boundary when its cache stamp is bad.
    interaction("""
      const invalid=payload('broken','KEPT',{scan_run_id:'old-run',scan_last_attempt_at:t1});
      queue.push({body:invalid}); render(options()); await settle();
      queue.push({body:invalid},{status:500,body:{}},{body:invalid});
      await clickStart(); await settle(); assert.ok(feed.error);
      queue.push({body:payload(t2,null,{scan_run_id:'new-run',scan_last_attempt_at:null})});
      await feed.refresh(); await settle();
      assert.equal(feed.error,null, 'Invalid old cache stamp hid a verified later run');
      assert.equal(feed.info.cached_at,t2); assert.equal(toasts.length,0);
    """, setup="")


def test_direction_change_drops_old_start_failure_before_effects_settle():
    # Leaking the previous direction's attempt error mislabels the new tab.
    interaction("""
      queue.push({body:old},{status:401,body:{}},{body:old});
      await clickStart(); await settle(); assert.ok(feed.error);
      const short=defer(); queue.push({promise:short.promise});
      render(options({scopeKey:'bi:short',resultsUrl:'/results?direction=short',startBody:{direction:'short'}}));
      assert.equal(feed.error,null); assert.equal(feed.results.length,0);
      short.resolve(response(200,payload(t2,'SHORT',{scan_run_id:'short-run',scan_last_attempt_at:t2})));
      await settle(); assert.equal(feed.error,null); assert.equal(feed.results[0].ticker,'SHORT');
    """)


@pytest.mark.parametrize("late", [
    {"status": 429, "body": {"retry_after_seconds": 90}},
    {"status": 200, "body": {"status": "unexpected"}},
])
def test_late_failed_post_cannot_attach_start_error_or_refresh_to_new_direction(late):
    # Missing scope/operation ownership lets an old click mutate the new feed.
    interaction("""
      const post=defer(); queue.push({body:old},{promise:post.promise});
      const pending=clickStart(); await settle();
      queue.push({body:payload(t2,'SHORT',{scan_run_id:'short-run',scan_last_attempt_at:t2})});
      render(options({scopeKey:'bi:short',resultsUrl:'/results?direction=short',startBody:{direction:'short'}}));
      await settle();
      post.resolve(response(LATE.status,LATE.body)); await pending; await settle();
      assert.equal(feed.error,null); assert.equal(feed.results[0].ticker,'SHORT');
      assert.equal(feed.isScanning,false); assert.equal(timers.size,0);
      assert.equal(requests.filter(r=>r.url.includes('short')).length,1);
    """.replace("LATE", json.dumps(late)))
