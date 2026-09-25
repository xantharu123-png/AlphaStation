# SMTP rejection and recipient recovery

## Scope

This repair separates a failed delivery from a missing valid signal. It does
not change scanner conditions, scores, level geometry, recipient permissions,
or trade-mail eligibility. No delayed trade-message replay is introduced.

## Definite rejection versus uncertain delivery

Previously, an explicit negative MAIL/DATA reply could take the generic
unknown-delivery path. That retained the pending tracker intent and placed its
delivery key in non-expiring quarantine, blocking a later fresh scan decision.

Only stage-specific `SMTPSenderRefused` and `SMTPDataError` with a 4xx/5xx code
now take the existing definite-failure cleanup path. In the API, the current
send ends; another scanner decision must revalidate before preparing another
delivery. The background sender retains its existing bounded retry policy for
definite failures.

A generic `SMTPResponseException` is **not** sufficient evidence of refusal:
Python can synthesize code 500 for an oversized response line, including after
DATA transmission. That case, timeouts, disconnects and unexpected codes keep
the existing uncertainty quarantine. QUIT cleanup after successful DATA
acceptance cannot downgrade acceptance or cause a duplicate send.

Historical uncertain records are not deleted or automatically reclassified.
They require separate evidence of the original transaction outcome.

## Recipient-cohort interruption

An SMTP 421 reply during RCPT can abort the envelope after some recipients were
accepted at the RCPT stage and before others were examined. No DATA has been
accepted for any of them. Retrying only addresses listed in the exception can
silently omit both earlier and later recipients.

The API repair retains the original pending cohort, removing only explicit
permanent refusals. It never adds addresses from exception text and never
reintroduces recipients already accepted by an earlier DATA transaction.
Existing attempt limits remain in force.

The separate background transport has the same RCPT-stage boundary: an
exception means no recipient received DATA in that transaction. Its result
must therefore report all intended recipients as unaccepted, never infer
acceptance from addresses missing in the exception. Both SSL and STARTTLS
paths follow that rule. They also distinguish stage-specific negative replies
from uncertain parser failures, just like the API transport.

## Accurate stock-mail diagnostics

The stock mail aggregator now records whether any row passed final validation
separately from whether sending succeeded. A sender failure no longer adds the
misleading `no_mail_adjacent_revalidated_rows` event. If all rows genuinely
fail final validation, the existing no-valid-rows diagnostic remains.

## Verification and deployment boundary

Regression tests use isolated tracker, outbox and dedupe stores, fake
credentials and controlled SMTP transport. They exercise definite rejection,
ambiguous acceptance, safe later recovery, recipient cohorts and QUIT errors.
No real SMTP message or production state mutation is part of those tests.

Production signal suppression and actual receipt must be checked separately;
these transport defects alone do not establish why a specific live signal
was not sent. Private production exports are not included in this document.

### Executed checks

- Targeted transport/tracker/outbox/notification run: **135 passed**.
- Independent combined API/background patch review run: **83 passed**.
- Frozen-source broad suite: **8,890 passed, 1 failed**, 647.48 seconds.
  The failure was the unchanged Windows process-start/exit timing assertion in
  `test_suppression_telemetry.py`: the spawned child's `exitcode` was still
  `None` after a five-second join. The child subsequently exited; the complete
  unchanged test module passed separately (**59 passed**, 33.76 seconds).
  This is not described as a clean single full-suite run. Neither its timeout
  nor production locking behavior was modified to force a passing result.
- The broad Windows command explicitly excluded `test_deploy_auto_update.py`
  and `test_deploy_migration.py`, the same Linux deployment-module exclusions
  used for the preceding baseline. These mail changes do not alter deployment
  scripts. Other deployment/source-contract tests were included.
- One known pytest import-rewrite warning concerns the offline harness's eager
  API initialization. `git diff --check` is clean.

All runs above used the private offline harness with external network/SMTP
blocked and disposable state. Earlier broad runs were interrupted while new
review findings were being corrected; they are not counted as completed runs.
