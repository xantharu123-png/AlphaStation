# Manual scan admission: distinguish a blocked start from an existing run

## Confirmed incident

The supplied server log showed a completed Cup-and-Handle run taking about
14 minutes, with 0 of 121 preselected candidates passing the special check.
Three later manual Cup requests were rejected because the stock engine was
owned by `biotech`. These requests did not execute one-second universe scans.

`_run_scan_safe` correctly rejected overlapping heavy stock work. However,
`_manual_scan_ack` described every rejection as `already_running` and returned
the requested scanner's old run ID. The generic scanner feed followed that
old run and immediately redisplayed its previous error. The process ID also
changed between the supplied log excerpts; the cause and completion of the
intervening run are not established by these excerpts.

## Repaired contract

| Admission evidence | Response | Client action |
| --- | --- | --- |
| Start accepted | `started`, accepted true | Follow the accepted run |
| Own worker still alive | `already_running`, accepted false | Follow that worker; no duplicate-start claim |
| Different shared-engine owner | `busy`, accepted false, owner key | Show not-started notice; do not poll an old requested run |
| Rejection but no longer a proven owner | `busy`, `start_not_accepted` | Show not-started notice; do not fabricate a running worker |

Busy responses contain neither a run ID nor an attempt time from the old
requested run. Owner checks are read-only under the existing scan lock.
Admission, watchdogs, scheduler intervals and mutual-exclusion rules are
unchanged. No new queue or overlapping replacement worker is introduced.

The refusal appears above the historical scan status, even if that status is
an error. Refreshing the same old result does not erase the refusal. A later
observed run, another start request or a strategy switch clears it. Only
allowlisted owner labels are displayed; arbitrary provider/server messages
are not interpolated. Existing errors and historical results are preserved.

## Verification

- 33 new admission/acknowledgement cases, executing the actual admission
  functions with inert thread probes and no provider/cache/mail I/O.
- Nine additional frontend regressions: Biotech/automatic/crypto blockers,
  unknown owner labels, old failed-run refresh, retry, later automatic result,
  strategy switches and visible error-state notices.
- 8,122 tests passed, three skipped in the isolated offline suite. The
  Linux/Bash deployment test file was excluded from that Windows run.
- Frontend bundle generated and hash verified: `d80cffee2486`.
- Independent source review and 156 frontend lifecycle/control checks passed.
- Playwright CLI: actual compiled frontend on localhost, simulated API only,
  all external requests blocked; start click and subsequent status refresh
  checked at 1440x1000 and 390x844. Refusal remains visible, start button returns
  to idle, no horizontal page overflow at the mobile viewport. No console
  errors; only the pre-existing Tailwind development warning.
- QA screenshots and routes remain private under `output/playwright/`.

## Scope and deployment boundary

This fixes the acknowledgement/feed contract used by `/api/scan` and
`/api/bi-scan`. It does not change Cup detection, data-validation policy,
signal eligibility, subscriptions or SMTP. It does not establish delivery of
a new signal mail or explain the earlier market-data error.

Some older dedicated scanner tabs still use their own start endpoints and
do not consume this acknowledgement contract; they are not claimed repaired
by this change. A follow-up should audit/migrate those endpoints and their
clients together, without falsely marking a rejected request as started.

No production scan, restart or deployment was performed during this repair.
After deployment, a refused Cup start while another heavy worker runs should
identify that worker without following an old Cup error. A complete fresh
Cup run and signal delivery remain separate production observations.
