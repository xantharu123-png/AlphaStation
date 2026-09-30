# BI progress tied to the current scan

This continuation verifies the deployed mail diagnostics and repairs the open
BI progress-display defect. It changes neither signal admission nor mail
eligibility. BI still requires at least 17 of 20 confirmed indicators.

## Confirmed production state

On 30 September at 17:49 UTC, the public health response was healthy on
`d06b8af58f35`, with frontend bundle `2ebd16934df3`. Local HEAD and the remote
main branch matched that revision. The operator had therefore already deployed
both `716f2eb` and `d06b8af`; another pull of those fixes was unnecessary.

The authenticated mail panel at 18:05 UTC showed one swing recipient, six
skipped decisions, zero SMTP acceptances, zero sending errors and an empty
outbox. Its window is at most 50 decisions since the current API start and
at most 24 hours. It is not a complete historical delivery report. The visible
reasons included low score, missing entry/trigger values, no-chase restrictions
and persistent duplicate protection. No test message or old signal replay was
sent during this verification.

A refresh at 18:14 UTC showed eight skipped decisions and still zero SMTP
acceptances, zero sending errors and no queued mail. It included a new Momentum
decision rejected before transport on score/daily-move/candle/target checks.
This confirms continued eligibility evaluation, not successful email delivery.

Biotech still displayed its roughly 20-hour-old result. Its 12 stored candidates
failed the newer news-contract check. A new complete Biotech result and actual
signal-mail receipt remain unverified. The earlier provider HTTP 401 concerns
BPIQ catalyst access; no credentials, entitlement or account settings changed.
At 18:21 UTC the scheduler panel showed the stock strategy round completed
roughly 12 minutes earlier, BI Long running, and lightweight checks continuing.
Biotech still had no new completed result. A running BI task is not evidence
that Biotech has started or succeeded.

## Reproduction and repair

The BI producer wrote useful counters independently of its result cache, but
without the scheduler worker identity. The status route also discarded the
timestamp. The frontend deliberately rejected these unbound counters. Since a
partial result cache was first written after a valid hit, a working scan with
zero hits could display no numeric progress for its entire duration.

BI Long, BI Short and Biotech now attach the executing worker's scanner key and
run ID to their progress files. An unbound or finished worker cannot borrow a
different live worker's identity. Biotech progress publication is also atomic;
a failed replacement preserves the previous complete snapshot.

The status route requires matching scanner, run and live control state. It
checks the timestamp against the actual run start and server observation time,
and rejects invalid, missing or impossible counters. It reports the age of the
last measurement rather than fabricating a current timestamp. A paused or slow
worker can therefore retain an honestly dated checkpoint; a new run cannot
adopt that old checkpoint.

The frontend validates the same identity before showing file-based progress.
Current counters are independent of cached result rows. No cached candidate is
promoted to a signal by this change. A duplicated BI label that said
"Scan laeuft" even during a confirmed pause has been removed; the main control
remains the single progress/status display.

## Verification

Four initial regressions reproduced missing producer ownership and missing
zero-hit frontend progress. A further regression reproduced the misleading
paused-run label found in browser inspection. After repair, 205 focused tests
passed, including real bounded scheduler threads, API projection, frontend
rendering, pause ownership and startup recovery.

Local browser checks used synthetic responses with all writes disabled and no
upstream network. The actual frontend showed 1500 of 5300 checked, zero hits,
28 percent progress; a stale Short run did not inherit those counters. A paused
Long run retained the same checkpoint and showed its resume control. The mobile
layout was inspected at 390 pixels without page-level horizontal overflow.
An ended run showed its completed-result state without a running progress bar.

The final frozen full suite passed **9,761 tests**, with **five platform skips**,
no failures and the existing eager-import `anyio` warning, in **1,439.70 seconds**.
All seven changed code/test file SHA256 values matched before and after the run.
The frontend source/bundle marker is `15fd2ea4752d`; JavaScript syntax and scoped
diff checks passed. The skips concern Windows symlink privileges or Linux/POSIX
filesystem contracts, not ignored scanner or delivery failures.

The existing offline harness was used for both the focused selection and full
suite: `tmp/offline_mail_fix_tests_20260925.py`, with `-q --tb=short`. It uses
disposable local databases, fake credentials and blocked external network/SMTP.
The full checkout includes inherited deployment-script retirement work; those
changes are preserved separately and are not part of this progress commit.

An earlier in-progress full run was deliberately stopped before removing the
browser-discovered duplicate pause label; it is not a completed acceptance
result. Publication and the subsequent operator deployment remain distinct.

## Remaining deployment and delivery checks

This progress repair must be deployed before its run-bound producer metadata
can appear on Hetzner. No server restart, scan start, production data change,
installation migration or credential change was performed here. Existing local
deployment-script retirement work is preserved separately, not bundled into
this repair. Verify a newly completed Biotech run and a legitimately admitted
mail's SMTP acceptance and inbox receipt separately.
