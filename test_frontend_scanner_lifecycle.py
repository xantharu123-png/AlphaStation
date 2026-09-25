"""Execute shipped scanner guards and hook lifecycle with synthetic fetches.

The hook harness models state/effect dependencies and out-of-order responses;
rendered browser QA remains a separate acceptance step. No API import or network.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
START = SOURCE.index("function scannerPublicFailure(")
PURE = SOURCE[START:SOURCE.index("function useScannerFeed(", START)]
HOOK = SOURCE[START:SOURCE.index("function ScannerEvidence(", START)]


def node_run(code):
    node = shutil.which("node")
    assert node, "Node is required for scanner lifecycle verification"
    completed = subprocess.run(
        [node], input=code, encoding="utf-8", capture_output=True, check=True,
        timeout=20,
    )
    return completed.stdout.strip()


def evaluate(expression):
    return json.loads(node_run(PURE + "\nconsole.log(JSON.stringify(" + expression + "));"))


@pytest.mark.parametrize("status,payload,fragment", [
    (400, {"detail": "apiKey=SECRET"}, "Konfiguration"),
    (401, {"detail": "SECRET"}, "Anmeldung"),
    (403, {"detail": "SECRET"}, "Berechtigung"),
    (429, {"retry_after_seconds": 90, "detail": "SECRET"}, "90 Sekunden"),
    (500, {"detail": "SECRET"}, "HTTP 500"),
    (501, {"detail": {"code": "market_scanner_not_implemented", "message": "SECRET"}}, "noch nicht verfügbar"),
    (429, {"detail": {"code": "scan_provider_rate_limited", "retry_after_seconds": 25, "message": "SECRET"}}, "25 Sekunden"),
    (503, {"detail": {"code": "scan_data_incomplete", "message": "SECRET"}}, "kein Scan mit null"),
    (200, {"scan_error": "scan_provider_unauthorized"}, "Marktdaten-Zugriff"),
    (200, {"scan_error": "scan_data_incomplete"}, "kein Scan mit null"),
    (200, {"scan_error": "apiKey=SECRET"}, "fehlgeschlagen"),
])
def test_public_failures_are_actionable_without_raw_provider_or_auth_errors(status, payload, fragment):
    result = evaluate(f"scannerPublicFailure({status}, {json.dumps(payload)})")
    assert fragment in result
    assert "SECRET" not in result


@pytest.mark.parametrize("value", [-1, "apiKey=SECRET", 900000, None])
def test_cooldown_never_echoes_untrusted_or_unbounded_values(value):
    result = evaluate(f"scannerPublicFailure(429, {{retry_after_seconds:{json.dumps(value)}}})")
    assert "SECRET" not in result
    assert "900000" not in result
    assert "-1" not in result


@pytest.mark.parametrize("data,previous,saw_running,run_id,expected", [
    ({"cached_at": "2026-09-08T10:00:00", "scan_error": "scan_failed"}, None, True, None, "error"),
    ({"cached_at": "2026-09-08T10:00:00", "scan_running": True}, None, False, None, "running"),
    ({"cached_at": "2026-09-08T10:00:00", "partial": True}, None, False, None, "running"),
    ({"cached_at": "2026-09-08T10:00:00"}, "2026-09-08T10:00:00", False, None, "pending"),
    ({"cached_at": "2026-09-08T10:00:00"}, "2026-09-08T10:00:00", True, None, "missing_final"),
    ({"cached_at": "2026-09-08T09:00:00"}, "2026-09-08T10:00:00", False, None, "pending"),
    ({"cached_at": "2026-09-08T12:00:00+02:00"}, "2026-09-08T10:00:00Z", False, None, "pending"),
    ({"cached_at": "2026-09-08T10:01:00Z", "diagnostics": {"coverage": "complete"}}, "2026-09-08T10:00:00", False, None, "complete"),
    ({"cached_at": "2026-09-08T10:00:00", "diagnostics": {"coverage": "complete"}}, None, False, None, "complete"),
    ({"cached_at": "2026-09-08T10:00:00", "data": []}, None, False, None, "unverified_final"),
    ({"cached_at": "2026-09-08T10:00:00", "diagnostics": {"coverage": "incomplete"}}, None, False, None, "error"),
    ({"cached_at": "broken"}, None, False, None, "pending"),
    ({"cached_at": "2026-09-08T11:00:00", "scan_run_id": "other"}, "2026-09-08T10:00:00", True, "ours", "superseded"),
])
def test_scan_completion_requires_new_final_evidence(data, previous, saw_running, run_id, expected):
    args = ",".join(json.dumps(value) for value in (data, previous, saw_running, run_id))
    assert evaluate(f"scannerPollOutcome({args})") == expected


@pytest.mark.parametrize("overrides,expected", [
    ({"loading": True, "hasLoaded": False}, "loading"),
    ({"hasLoaded": True, "info": {}}, "missing"),
    ({"hasLoaded": True, "info": {"cached_at": "broken"}}, "missing"),
    ({"error": "HTTP failure"}, "error"),
    ({"running": True}, "running"),
    ({"info": {"cached_at": "2026-09-08T10:00:00", "warnings": ["Cache alt: 5h"]}}, "stale"),
    ({"info": {"cached_at": "2026-09-08T10:00:00", "diagnostics": {"raw_cache_rows": 3, "validated_scanner_signals": 0}}}, "warning"),
    ({"info": {"cached_at": "2026-09-08T10:00:00", "diagnostics": {"validated_scanner_signals": 2, "decorated_scanner_signals": 0}}}, "warning"),
    ({"info": {"cached_at": "2026-09-08T10:00:00"}}, "warning"),
    ({"info": {"cached_at": "2026-09-08T10:00:00", "diagnostics": {"funnel": {"coverage": "incomplete"}}}}, "warning"),
    ({}, "empty"),
    ({"count": 2}, "ready"),
])
def test_empty_copy_distinguishes_loading_error_cache_and_verified_zero(overrides, expected):
    payload = {"info": {"cached_at": "2026-09-08T10:00:00", "diagnostics": {"funnel": {"coverage": "complete"}}},
               "loading": False, "running": False, "error": None, "hasLoaded": True, "count": 0, "kind": "bi"}
    payload.update(overrides)
    result = evaluate(f"scannerEvidenceState({json.dumps(payload)})")
    assert result["tone"] == expected


def test_rejection_summary_only_exposes_aggregate_allowlisted_labels():
    payload = {"funnel": {"coverage": "complete", "rejected": {
        "indicator_or_hard_gate_contract": 41, "plan:secret": 5,
        "apiKey=SECRET": 2, "invalid": "raw secret", "null": None,
    }}}
    result = evaluate(f"scannerDiagnosticSummary({json.dumps(payload)})")
    text = json.dumps(result)
    assert "SECRET" not in text and "secret" not in text
    assert result["rejected"][0] == ["17/20 oder harter BI-Pruefpunkt nicht erfuellt", 41]


@pytest.mark.parametrize("info,loading,has_loaded", [
    (None, False, False),
    ({"cached_at": None}, False, True),
    ({"cached_at": "broken"}, False, True),
    ({"cached_at": "2026-09-08T10:00:00"}, True, True),
    ({"cached_at": "2026-09-08T10:00:00", "partial": True}, False, True),
])
def test_bi_count_header_never_reports_zero_without_confirmed_final_snapshot(info, loading, has_loaded):
    payload = {"info": info, "loading": loading, "hasLoaded": has_loaded, "count": 0}
    assert evaluate(f"biSnapshotCountLabel({json.dumps(payload)})") == "Anzahl noch nicht bestaetigt"


@pytest.mark.parametrize("count", [0, 2])
def test_bi_count_header_preserves_real_final_zero_and_positive_counts(count):
    payload = {"info": {"cached_at": "2026-09-08T10:00:00", "partial": False}, "loading": False, "hasLoaded": True, "count": count}
    assert evaluate(f"biSnapshotCountLabel({json.dumps(payload)})") == f"{count} BI-Signale im angezeigten Stand (mindestens 17/20)"
    assert "<span>{biSnapshotCountLabel({ info: liveInfo," in SOURCE


HARNESS = r"""
const assert = require('assert/strict');
let slots = [], cursor = 0, dirty = false, effects = [], pendingEffects = [], feed, opts;
let timers = new Map(), nextTimer = 1, queue = [], requests = [], toasts = [];
global.window = {addToast:(...args) => toasts.push(args)};
global.setTimeout = fn => { const id = nextTimer++; timers.set(id, fn); return id; };
global.clearTimeout = id => timers.delete(id);
global.useState = initial => {
  const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
  return [slots[i], value => { const next = typeof value === 'function' ? value(slots[i]) : value; if (!Object.is(next, slots[i])) { slots[i] = next; dirty = true; } }];
};
global.useRef = initial => { const i = cursor++; if (!(i in slots)) slots[i] = {current:initial}; return slots[i]; };
global.useEffect = (fn, deps) => {
  const i = cursor++; const old = effects[i];
  if (!old || deps.some((d, n) => !Object.is(d, old.deps[n]))) pendingEffects.push(() => {
    if (old?.cleanup) old.cleanup(); effects[i] = {deps, cleanup:fn()};
  });
};
global.fetch = (url, init = {}) => {
  requests.push({url, init});
  assert.ok(queue.length, 'Unexpected fetch: ' + url);
  const next = queue.shift();
  if (next.promise) return next.promise;
  if (next.error) return Promise.reject(new TypeError('network unavailable'));
  return Promise.resolve(response(next.status || 200, next.body));
};
function response(status, body) { return {ok:status >= 200 && status < 300, status, json:async()=>body, headers:{get:()=>null}}; }
function payload(stamp, ticker = null, extra = {}) {
  return {cached_at:stamp, data:ticker ? [{ticker}] : [], scan_running:false, partial:false,
          diagnostics:{raw_cache_rows:ticker ? 1 : 0, validated_scanner_signals:ticker ? 1 : 0,
                       decorated_scanner_signals:ticker ? 1 : 0, visible_scanner_signals:ticker ? 1 : 0,
                       funnel:{coverage:'complete', total:20, checked:20}}, ...extra};
}
const t1 = '2026-09-08T10:00:00', t2 = '2026-09-08T10:05:00';
function options(overrides = {}) { return {scopeKey:'bi:long', resultsUrl:'/results?direction=long', startUrl:'/start', startBody:{direction:'long'},
  cachePrefix:'bi', scanCache:{}, updateScanCache:()=>{}, schedulerState:{last_run:t1,running:false}, ...overrides}; }
function render(next = opts) {
  opts = next; cursor = 0; dirty = false; pendingEffects = [];
  feed = useScannerFeed(opts);
  const pending = pendingEffects; pendingEffects = []; pending.forEach(fn => fn());
}
async function settle() { for (let i = 0; i < 30; i++) { await Promise.resolve(); if (dirty) render(); } }
async function timer() { assert.ok(timers.size, 'Expected scheduled poll'); const [id, fn] = timers.entries().next().value; timers.delete(id); fn(); await settle(); }
function defer() { let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve}; }
"""


def lifecycle(code):
    node_run(HOOK + HARNESS + "\n(async()=>{\n" + code + "\n})().catch(e=>{console.error(e);process.exitCode=1});")


def test_bi_auto_completion_refreshes_previously_empty_open_tab():
    lifecycle("""
      queue.push({body:payload(t1)}); render(options()); await settle();
      assert.equal(feed.results.length, 0);
      queue.push({body:payload(t2, 'NEW')});
      render({...opts, schedulerState:{last_run:t2,running:false}}); await settle();
      assert.equal(requests.length, 2); assert.equal(feed.results[0].ticker, 'NEW');
    """)


def test_background_completion_notice_cannot_outlive_later_refreshed_zero_result():
    lifecycle("""
      queue.push({body:payload(t1)}); render(options()); await settle();
      queue.push({body:payload(t1,null,{scan_running:true,partial:true,scan_run_id:'background-1'})});
      render({...opts,schedulerState:{last_run:t1,running:true}}); await settle();
      queue.push({body:payload(t2,'ONE',{scan_run_id:'background-1'})}); await timer();
      assert.equal(feed.results.length,1); assert.equal(feed.notice,null);
      const t3='2026-09-08T10:10:00';
      queue.push({body:payload(t3,null,{scan_run_id:'background-2'})});
      render({...opts,schedulerState:{last_run:t3,running:false}}); await settle();
      assert.equal(feed.results.length,0); assert.equal(feed.info.cached_at,t3);
      assert.equal(feed.notice,null);
      const evidence=scannerEvidenceState({info:feed.info,loading:feed.loading,running:feed.isScanning,error:feed.error,hasLoaded:feed.hasLoaded,count:feed.results.length,kind:'bi'});
      assert.equal(evidence.tone,'empty'); assert.equal(toasts.length,0);
    """)


def test_fetch_failure_preserves_last_good_rows_and_displays_safe_error():
    lifecycle("""
      queue.push({body:payload(t1, 'KEPT')}); render(options()); await settle();
      queue.push({status:500,body:{detail:'apiKey=SECRET'}}); await feed.refresh(); await settle();
      assert.equal(feed.results[0].ticker, 'KEPT'); assert.equal(feed.info.cached_at, t1);
      assert.match(feed.error, /HTTP 500/); assert.ok(!feed.error.includes('SECRET'));
    """)


def test_initial_read_failure_cannot_be_presented_as_no_17_of_20_signals():
    lifecycle("""
      queue.push({status:401,body:{detail:'SECRET'}}); render(options()); await settle();
      assert.equal(feed.hasLoaded, false); assert.match(feed.error, /Anmeldung/);
      const state=scannerEvidenceState({info:feed.info,error:feed.error,hasLoaded:feed.hasLoaded,count:0,kind:'bi'});
      assert.equal(state.tone, 'error'); assert.ok(!state.text.includes('17/20'));
    """)


def test_post_cooldown_stops_without_empty_success_or_erasing_snapshot():
    lifecycle("""
      queue.push({body:payload(t1, 'KEPT')}); render(options()); await settle();
      queue.push({body:payload(t1, 'KEPT')},{status:429,body:{retry_after_seconds:90,detail:'SECRET'}});
      await feed.start(); await settle();
      assert.equal(feed.isScanning, false); assert.equal(feed.results[0].ticker, 'KEPT');
      assert.match(feed.error, /90 Sekunden/); assert.equal(toasts.length, 0); assert.equal(timers.size, 0);
    """)


def test_missing_baseline_does_not_launch_an_untrackable_scan():
    lifecycle("""
      queue.push({body:payload(t1)}); render(options()); await settle();
      queue.push({status:503,body:{}}); await feed.start(); await settle();
      assert.equal(requests.filter(r=>r.init.method==='POST').length,0);
      assert.equal(feed.isScanning,false); assert.ok(feed.error);
    """)


def test_old_cache_does_not_complete_request_and_later_worker_failure_is_not_success():
    lifecycle("""
      queue.push({body:payload(t1, 'KEPT')}); render(options()); await settle();
      queue.push({body:payload(t1, 'KEPT')},{body:{status:'started',accepted:true,run_id:'ours'}});
      await feed.start(); await settle();
      queue.push({body:payload(t1,'KEPT',{scan_run_id:'ours'})}); await timer();
      assert.equal(feed.isScanning,true); assert.equal(toasts.length,0);
      queue.push({body:payload(t1,'KEPT',{scan_run_id:'ours',scan_running:true})}); await timer();
      queue.push({body:payload(t1,'KEPT',{scan_run_id:'ours',scan_error:'scan_data_unavailable'})}); await timer();
      assert.equal(feed.isScanning,false); assert.ok(feed.error); assert.equal(toasts.length,0);
    """)


def test_genuine_new_final_zero_can_complete_without_inventing_trades():
    lifecycle("""
      queue.push({body:payload(t1, 'OLD')}); render(options()); await settle();
      queue.push({body:payload(t1, 'OLD')},{body:{status:'started',accepted:true,run_id:'ours'}});
      await feed.start(); await settle();
      queue.push({body:payload(t2,null,{scan_run_id:'ours'})}); await timer();
      assert.equal(feed.results.length,0); assert.equal(feed.isScanning,false); assert.equal(feed.error,null);
      assert.equal(toasts.length,1); assert.equal(toasts[0][1],'success');
    """)


def test_busy_ack_does_not_claim_to_have_launched_another_worker():
    lifecycle("""
      queue.push({body:payload(t1)}); render(options()); await settle();
      queue.push({body:payload(t1,null,{scan_running:true})},{body:{status:'already_running',accepted:false,run_id:'existing'}});
      await feed.start(); await settle(); assert.match(feed.notice,/kein zweiter Lauf/);
      queue.push({body:payload(t2,'NEW',{scan_run_id:'existing'})}); await timer();
      assert.equal(feed.results[0].ticker,'NEW'); assert.equal(toasts.length,0);
    """)


@pytest.mark.parametrize("owner,label", [
    ("biotech", "Biotech"), ("strategy_scan", "Automatische Aktienrunde"),
    ("crypto_trade_signals", "Crypto Trade Signals"), ("apiKey=SECRET", "Ein anderer Scanner"),
    ("__proto__", "Ein anderer Scanner"),
])
def test_other_worker_refusal_never_polls_old_failed_run_and_survives_refresh(owner, label):
    lifecycle("""
      const old=payload(t1,null,{scan_run_id:'old-cup',scan_error:'scan_data_invalid'});
      queue.push({body:old}); render(options()); await settle();
      queue.push({body:old},{body:{status:'busy',accepted:false,run_id:null,
        reason:'other_scanner_running',blocking_scan_key:OWNER,message:'apiKey=SECRET'}});
      await feed.start(); await settle();
      assert.equal(feed.isScanning,false); assert.equal(timers.size,0);
      assert.match(feed.blockedStart,/Nicht gestartet/);
      assert.ok(feed.blockedStart.includes(LABEL)); assert.ok(!feed.blockedStart.includes('SECRET'));
      assert.equal(feed.notice,null); assert.equal(feed.info.scan_run_id,'old-cup');
      assert.equal(toasts.length,0); assert.equal(requests.length,3);
      const refusal=feed.blockedStart;
      // ScanControl performs this refresh after every start attempt.
      queue.push({body:old}); await feed.refresh(); await settle();
      assert.equal(feed.blockedStart,refusal); assert.ok(feed.error);
      assert.equal(feed.isScanning,false); assert.equal(timers.size,0);
      assert.equal(toasts.length,0); assert.equal(requests.length,4);
    """.replace("OWNER", json.dumps(owner)).replace("LABEL", json.dumps(label)))


def test_refused_start_can_be_retried_without_following_old_error():
    lifecycle("""
      const old=payload(t1,null,{scan_run_id:'old-cup',scan_error:'scan_data_invalid'});
      queue.push({body:old}); render(options()); await settle();
      queue.push({body:old},{body:{status:'busy',accepted:false,run_id:null,reason:'start_not_accepted'}});
      await feed.start(); await settle();
      assert.match(feed.blockedStart,/nicht angenommen/); assert.equal(timers.size,0);
      queue.push({body:old},{body:{status:'started',accepted:true,run_id:'new-cup'}});
      await feed.start(); await settle();
      assert.equal(feed.blockedStart,null); assert.equal(feed.isScanning,true);
      queue.push({body:payload(t2,'NEW',{scan_run_id:'new-cup'})}); await timer();
      assert.equal(feed.isScanning,false); assert.equal(feed.results[0].ticker,'NEW');
      assert.equal(toasts.length,1);
    """)


def test_later_observed_run_clears_previous_refused_start_without_claiming_manual_success():
    lifecycle("""
      const old=payload(t1,null,{scan_run_id:'old-cup',scan_error:'scan_data_invalid'});
      queue.push({body:old}); render(options()); await settle();
      queue.push({body:old},{body:{status:'busy',accepted:false,run_id:null,
        reason:'other_scanner_running',blocking_scan_key:'biotech'}});
      await feed.start(); await settle(); assert.ok(feed.blockedStart);
      queue.push({body:payload(t2,'AUTO',{scan_run_id:'new-auto'})});
      await feed.refresh(); await settle();
      assert.equal(feed.blockedStart,null); assert.equal(feed.error,null);
      assert.equal(feed.results[0].ticker,'AUTO'); assert.equal(toasts.length,0);
    """)


def test_strategy_switch_clears_refused_start_and_late_refusal_cannot_leak():
    lifecycle("""
      queue.push({body:payload(t1)}); render(options()); await settle();
      queue.push({body:payload(t1)},{body:{status:'busy',accepted:false,reason:'other_scanner_running',blocking_scan_key:'biotech'}});
      await feed.start(); await settle(); assert.ok(feed.blockedStart);
      queue.push({body:payload(t2,'SHORT')});
      render(options({scopeKey:'bi:short',resultsUrl:'/results?direction=short'})); await settle();
      assert.equal(feed.blockedStart,null); assert.equal(feed.results[0].ticker,'SHORT');
      const slow=defer(); queue.push({body:payload(t2,'SHORT')},{promise:slow.promise});
      const pending=feed.start(); await settle();
      queue.push({body:payload(t1,'LONG')}); render(options()); await settle();
      slow.resolve(response(200,{status:'busy',accepted:false,blocking_scan_key:'biotech'}));
      await pending; await settle();
      assert.equal(feed.blockedStart,null); assert.equal(feed.results[0].ticker,'LONG');
    """)


def test_busy_notice_is_rendered_even_when_previous_scan_failed():
    evidence = SOURCE[SOURCE.index("function ScannerEvidence("):SOURCE.index("// Scanner Tab")]
    assert '{feed.blockedStart && <div' in evidence
    assert "data-testid={feed.blockedStart ? 'scanner-start-blocked' : undefined}" in evidence
    assert evidence.index('{compact.text}') < evidence.index('<div>{state.text}</div>')
    assert 'nicht das Ergebnis dieses Startversuchs' in evidence


def test_direction_change_ignores_late_previous_direction_response():
    lifecycle("""
      const slow=defer(); queue.push({promise:slow.promise}); render(options()); await settle();
      queue.push({body:payload(t2,'SHORT')});
      render(options({scopeKey:'bi:short',resultsUrl:'/results?direction=short',startBody:{direction:'short'}})); await settle();
      slow.resolve(response(200,payload(t1,'LONG'))); await settle();
      assert.equal(feed.results[0].ticker,'SHORT'); assert.equal(feed.info.cached_at,t2);
      assert.equal(requests[0].init.signal.aborted,true);
    """)


def test_failed_partial_poll_restores_previous_final_snapshot():
    lifecycle("""
      const cached={items:[{ticker:'FINAL'}],info:scannerSnapshotInfo(payload(t1,'FINAL'))};
      queue.push({body:payload(t2,null,{scan_running:true,partial:true,scan_run_id:'ours'})});
      render(options({scanCache:{'bi:snapshot:bi:long':cached}})); await settle();
      assert.equal(feed.results.length,0);
      queue.push({status:503,body:{}}); await timer();
      assert.equal(feed.results[0].ticker,'FINAL'); assert.equal(feed.info.cached_at,t1); assert.ok(feed.error);
    """)


def test_post_transport_uncertainty_does_not_claim_scan_was_not_started():
    lifecycle("""
      queue.push({body:payload(t1)}); render(options()); await settle();
      queue.push({body:payload(t1)},{error:true}); await feed.start(); await settle();
      assert.match(feed.error,/den Auftrag dennoch erhalten/); assert.equal(toasts.length,0);
    """)


def test_new_zero_without_coverage_never_gets_green_success_toast():
    lifecycle("""
      queue.push({body:payload(t1)}); render(options()); await settle();
      queue.push({body:payload(t1)},{body:{status:'started',accepted:true}});
      await feed.start(); await settle();
      queue.push({body:payload(t2,null,{diagnostics:{}})}); await timer();
      assert.equal(feed.isScanning,false); assert.equal(toasts.length,0);
      assert.match(feed.notice,/Pruefdetails fehlen/);
    """)


def test_late_post_ack_after_direction_change_cannot_start_wrong_direction_poll():
    lifecycle("""
      queue.push({body:payload(t1)}); render(options()); await settle();
      const slow=defer(); queue.push({body:payload(t1)},{promise:slow.promise});
      const started=feed.start(); await settle();
      queue.push({body:payload(t2,'SHORT')});
      render(options({scopeKey:'bi:short',resultsUrl:'/results?direction=short',startBody:{direction:'short'}})); await settle();
      slow.resolve(response(200,{status:'started',run_id:'long'})); await started; await settle();
      assert.equal(feed.results[0].ticker,'SHORT'); assert.equal(timers.size,0); assert.equal(feed.isScanning,false);
      assert.equal(requests.find(r=>r.init.method==='POST').init.signal.aborted,true);
    """)


def test_invalid_result_rows_preserve_snapshot_without_crashing_table():
    lifecycle("""
      queue.push({body:payload(t1,'KEPT')}); render(options()); await settle();
      queue.push({body:payload(t2,null,{data:[null]})}); await feed.refresh(); await settle();
      assert.equal(feed.results[0].ticker,'KEPT'); assert.match(feed.error,/Ungueltige Scanner-Antwort/);
    """)


def test_explicit_running_bi_worker_is_not_failed_after_30_minutes_of_polling():
    lifecycle("""
      queue.push({body:payload(t1,null,{scan_running:true,scan_run_id:'ours'})});
      render(options()); await settle();
      for (let attempt=0; attempt<905; attempt++) {
        queue.push({body:payload(t1,null,{scan_running:true,partial:true,scan_run_id:'ours'})});
        await timer();
      }
      assert.equal(feed.error,null); assert.equal(feed.isScanning,true); assert.equal(timers.size,1);
      queue.push({body:payload(t2,'FINISHED',{scan_run_id:'ours'})}); await timer();
      assert.equal(feed.error,null); assert.equal(feed.isScanning,false); assert.equal(feed.results[0].ticker,'FINISHED');
    """)


def test_unconfirmed_start_still_has_a_bounded_wait_without_false_success():
    lifecycle("""
      queue.push({body:payload(t1)}); render(options()); await settle();
      queue.push({body:payload(t1)},{body:{status:'started',accepted:true,run_id:'ours'}});
      await feed.start(); await settle();
      for (let attempt=0; attempt<902; attempt++) {
        queue.push({body:payload(t1,null,{scan_run_id:'ours'})}); await timer();
      }
      assert.match(feed.error,/Scanstatus konnte nicht/); assert.equal(feed.isScanning,false);
      assert.equal(timers.size,0); assert.equal(toasts.length,0);
    """)


def test_direction_change_hides_previous_rows_in_first_render_before_effect_update():
    lifecycle("""
      queue.push({body:payload(t1,'LONG')}); render(options()); await settle();
      assert.equal(feed.results[0].ticker,'LONG');
      const slow=defer(); queue.push({promise:slow.promise});
      render(options({scopeKey:'bi:short',resultsUrl:'/results?direction=short',startBody:{direction:'short'}}));
      // Deliberately inspect the render result before flushing effect setState.
      assert.equal(feed.results.length,0); assert.equal(feed.info,null);
      assert.equal(feed.hasLoaded,false); assert.equal(feed.loading,true); assert.equal(feed.error,null);
      slow.resolve(response(200,payload(t2,'SHORT'))); await settle();
      assert.equal(feed.results[0].ticker,'SHORT');
    """)


def test_both_tabs_use_shared_lifecycle_and_bi_refresh_is_direction_scoped():
    scanner = SOURCE[SOURCE.index("function ScannerTab("):SOURCE.index("function BIScannerTab(")]
    bi = SOURCE[SOURCE.index("function BIScannerTab("):SOURCE.index("function BiotechTab(")]
    assert "const feed = useScannerFeed({" in scanner and "const feed = useScannerFeed({" in bi
    assert "schedulerState: schedulerStatus?.scans?.[scannerKey] || null" in bi
    assert "<ScannerEvidence feed={feed}" in scanner and "<ScannerEvidence feed={feed}" in bi
    assert "scannerCompactEvidence({" in scanner and "scannerCompactEvidence({" in bi
    assert "Keine Stock-BI-Signale mit mindestens 17 von 20" not in bi


def test_stock_tab_binds_leaf_and_auto_scheduler_state_to_feed_and_controls():
    scanner = SOURCE[SOURCE.index("function ScannerTab("):SOURCE.index("function BIScannerTab(")]
    assert "schedulerState," in scanner
    assert "scanKey={scannerKey}" in scanner
    assert "'strategy_scan'" in scanner and "last_attempt_at" in scanner
    # Additional result views may use the same binding; their existence must
    # not invalidate the desktop/mobile scheduler contract.
    assert scanner.count("running: isScanning || !!schedulerState?.running") >= 2


@pytest.mark.parametrize("strategy", ["Momentum Breakout Long", "Gap Momentum Long", "Gap Momentum Short", "Cup and Handle Breakout"])
def test_stock_tab_scheduler_selection_preserves_manual_ownership_and_market_scope(strategy):
    scanner = SOURCE[SOURCE.index("function ScannerTab("):SOURCE.index("function BIScannerTab(")]
    selection = scanner[scanner.index("    const stratLower"):scanner.index("    const resultsUrl")]
    node_run(PURE + "\nconst assert=require('node:assert/strict');\n"
             + "function select(strategy,marketType,schedulerStatus) {\n" + selection
             + "\nreturn {scannerKey,schedulerState};}\n"
             + "const strategy=" + json.dumps(strategy) + ";\n" + """
      const manual='strat_'+strategy.toLowerCase().replace(/ /g,'_');
      const scans={strategy_scan:{running:false,last_attempt_at:'2026-09-14T12:00:00Z'}};
      assert.equal(select(strategy,'stocks',{scans}).scannerKey,'strategy_scan');
      scans[manual]={running:true,last_attempt_at:'2026-09-14T11:00:00Z'};
      scans.strategy_scan.running=true;
      assert.equal(select(strategy,'stocks',{scans}).scannerKey,manual);
      scans[manual]={running:false,last_attempt_at:'2026-09-14T13:00:00Z'};
      scans.strategy_scan.running=false;
      assert.equal(select(strategy,'stocks',{scans}).scannerKey,manual);
      assert.equal(select(strategy,'crypto',{scans}).scannerKey,'crypto_'+manual);
      const older={strategy_scan:{last_run:'2026-09-14T12:00:00Z'}};
      assert.equal(select(strategy,'stocks',{scans:older}).scannerKey,'strategy_scan');
    """)


def test_repeated_auto_failure_between_status_polls_refreshes_error_not_zero_success():
    lifecycle("""
      const failed=payload(t1,null,{scan_error:'scan_data_unavailable',diagnostics:{attempt_diagnostics:{coverage:'incomplete',final_results:null}}});
      queue.push({body:failed}); render(options({scopeKey:'stocks:Momentum Breakout Long',schedulerState:{running:false,last_run:t1,last_attempt_at:t1,run_id:'auto-1',scan_error:'scan_data_incomplete'}})); await settle();
      queue.push({body:failed});
      render({...opts,schedulerState:{...opts.schedulerState,last_attempt_at:t2,run_id:'auto-2'}}); await settle();
      assert.equal(requests.length,2); assert.equal(toasts.length,0);
      assert.match(feed.error,/kein Scan mit null Signalen/);
      const evidence=scannerEvidenceState({info:feed.info,loading:feed.loading,running:feed.isScanning,error:feed.error,hasLoaded:feed.hasLoaded,count:0});
      assert.equal(evidence.tone,'error');
    """)
