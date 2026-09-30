# Biotech data handling and scanner startup

## Production evidence

On 30 September the supplied server log showed a Biotech run ending with
`scan_data_incomplete` after 997 seconds. An earlier symbol-level error reached
the `results` array check in `_biotech_technical_score`. The original provider
response was not captured, so its exact validity cannot be established from
this traceback alone.

The same log separately showed BPIQ HTTP 401 responses. These concern the
optional catalyst provider, not SMTP. Fixing the actual account authorization
requires valid provider access; this patch does not change credentials.

The authenticated application was inspected without starting scans or sending
messages. Its refreshed mail panel showed five skipped events, zero SMTP
acceptances and zero sending errors in its displayed recent-event window.
That window is limited to the latest events since API startup, not the entire
historical delivery journal. The latest Momentum decisions cited quality,
extension, score or plan restrictions before SMTP. An 8-percent daily-move
restriction here limits extended moves; it is not a minimum gain requirement.

## Data correction

The Biotech technical path now uses the same strict daily-aggregate parser as
BI. A successful, consistent zero-result envelope can omit the optional
`results` array, as documented in the provider's
[daily aggregates contract](https://massive.com/docs/rest/stocks/aggregates/custom-bars).
The old required-list check incorrectly rejected that valid response shape.

Symbols with no completed history or fewer than 21 completed daily bars are
excluded before price, plan or score construction. Completion diagnostics count
these exclusions. Missing envelopes, contradictory counts, malformed OHLCV,
authorization failures and truncated responses still fail validation. Safe
reason codes now distinguish these cases in operator logs. This does not prove
which response the original SVA request contained.

## Startup and scheduling correction

Previously, startup deferred scans using file modification time alone, even
when the persisted stock cache belonged to an older contract. It also waited
inside a heavy scan for up to one hour before reaching recurring monitors.

- Startup now checks stock cache shape, completion and contract compatibility.
  The hourly strategy group also checks both constituent caches and uses the
  oldest timestamp, so a recent manual scan cannot hide an outdated sibling.
- The hourly aggregate now publishes its contract version.
- Heavy scans still have exclusive admission. Startup no longer blocks the
  scheduler while that worker runs; lightweight monitors and watchdog checks
  can continue.
- Due heavy workers are considered oldest-first, preventing a long hourly
  round from repeatedly overtaking initial BI or Biotech scans.

The fixed Gap schedule, pause ownership and mail validation rules are unchanged.

## Catalyst access failures

Concurrent BPIQ consumers share a locked fetch decision. HTTP 401 or 403 starts
a five-minute retry delay for the same credential, instead of repeating the
rejected call for every symbol. Changing the credential clears that delay.
The failure stays visible, does not advance the successful-refresh timestamp
and does not award stale-calendar bonuses. A successful later response restores
normal caching. No credential or credential digest is logged.

## Verification

New regressions initially reproduced nine failures. All checks use disposable
local state, fake credentials and blocked external network/SMTP.

- The full suite finished with 9,638 passed, five skipped and one failed in
  621.53 seconds. The sole failure was a source-text assertion expecting the
  exact old Biotech cache-save call without metadata keywords. Its adjacent
  ownership seal was still present.
- That assertion now checks the final publication and its preceding seal using
  Python's syntax tree, including that exactly one final publication exists.
  The existing live pause/discard test remains unchanged.
- After this test-only correction and an added retained-sibling scenario,
  all 267 focused tests passed. The scenario verifies that a valid sibling's
  score and price survive exclusion of a no-history symbol.
- Production source was unchanged between the full suite and the focused
  confirmation. The complete suite was not rerun after the test-only edits;
  these results are not presented as a second clean full-suite execution.
- The harness emits one pytest warning about eagerly imported `anyio`.

Coverage includes valid empty history, malformed envelopes, cache versions,
partial caches, stale sibling caches, nonblocking startup, exclusive workers,
fair heavy-job scheduling, concurrent catalyst consumers, bounded retries and
credential recovery. Existing transport, recipient and delivery-journal tests
remain separate from real inbox receipt.

## Deployment boundary

At the original inspection, Hetzner was healthy on `ce16f4de0374`.
The continuation on 30 September verified production health on `716f2ebd1e0c`
at 16:28 UTC: the operator has deployed these Biotech/startup corrections.
The hourly strategy round subsequently completed and BI Long was visibly
running alongside lightweight monitors. A new complete Biotech run was not
yet verified. No server restart, real scan, test message, credential change or
replay of old trade mail was initiated by this continuation.

Further diagnostic corrections and their separate deployment boundary are
documented in [Mail diagnostics](MAIL_DIAGNOSTIC_CAUSES_2026-09-30.md).

The existing request to remove the deployment installer is separate work in
the checkout. This repair does not use an installation migration or change
server ownership. Actual BPIQ access and a future mail-eligible signal's SMTP
acceptance and inbox receipt remain operational checks, not assumed outcomes
of passing tests.
