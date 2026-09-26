# Mail selection and reference-zone repair — 2026-09-25

## Evidence and scope

A private production export on the preceding revision shows a completed stock
sweep with 33 Momentum, 5 Gap Long, 10 Gap Short and zero Cup results. Only 40
rows reached the combined mail guard because of its per-strategy 25-row cap.
No sender event was recorded for that sweep. This establishes pre-send
suppression, not SMTP failure. Private exports and candidate data are not
included in this change.

## Bounded candidate selection

The sweep now passes all results within the existing leaf publication bounds
(150 Momentum and 50 per sibling) to one combined mail guard. It rejects an
over-bound or malformed leaf rather than publishing a silently truncated leaf.
Healthy siblings retain their existing independent completion behavior.

The stock mail helper considers at most 300 existing producer rows. The extra
rows require valid completed-daily swing provenance; existing live/premarket
enrichment keeps its first-50 window after provenance filters. Business fetches
retain their previous bounded allowance. No extra market-history requests are
introduced by the expanded cheap daily screening pass.

Only after the unchanged initial eligibility checks does the helper select the
best 50 candidates for claim ownership and final validation. Final sender-row
capacity remains ten, or two in YELLOW. WATCH shares the existing capacity;
failed and uncertain attempts consume capacity, and uncertain ownership is
not released as if no send occurred. Crypto input and claim caps are unchanged.

This repairs raw-rank starvation at positions 26, 51 and 76. It does not assert
that the omitted production rows were eligible; their full live geometry was
not available in the original export.

## Informational close labels cannot mutate real structure

Previously an informational PDC/PWC evidence item could merge into an actual
support/resistance cluster. Reproduced example: adding PDC at 100.035 widened
a resistance upper edge from 100.02 to 100.055 and erased its previously bound
close-breakout evidence. It also increased touch/source counts and strength.

Session-reference-only evidence now clusters separately and remains visible.
Adding it cannot change an actual zone's identity, bounds, confirmation time,
strength, touch counts, breakout certificate or retest certificate. Long/short,
daily/weekly, bridging, permutation and serialization cases are covered.

Real PDH/PDL, including the latest completed session's extrema, are **not**
exempted from the first-barrier rule. Their 1.35R room requirement remains.
The existing confirmed-close breakout rule still does not demand a retest.
Removing genuine nearby session highs/lows would be a separate strategy-policy
change, not this reference-label correction.

Follow-up on 2026-09-26: completed-daily signal snapshots now bind these
references to the session **preceding the signal**, while live snapshots keep
their latest-completed-session default. See
`DAILY_SIGNAL_MAIL_REPAIR_2026-09-26.md` for the reproduced self-barrier defect,
the explicit clock binding and the unchanged genuine-barrier risk gates.

Stock strategy cache generation advances from 11 to 12. Old geometry is
rejected by result and new-reminder lookup routes; it is not silently stamped
as repaired. Existing personal reminders keep their explicitly bound levels.
The scheduler's startup age check does not force immediate regeneration, so
a completed fresh strategy scan is required after deployment. Fresh Biotech
scans also build corrected zones; already-valid cached Biotech plans are not
retroactively rewritten. No cache files or reminder records are deleted.

## Mail diagnostics and private evidence

WATCH with no eligible recipient now reports `watch_no_eligible_recipients`,
rather than implying that global trading addresses or SMTP are broken. No
recipient fallback, permission, opt-in, subscription or credential changes.

The standalone read-only collector accepts up to 32 MiB only for fixed stock
strategy cache names, retaining the 8-MiB bound elsewhere, regular-file checks,
nonblocking/no-follow opens and inode/size/mtime stability checks. It exports
at most 50 scalar geometry samples per such cache with omission counts. No
tickers, recipients, zone IDs, free-text fields or raw market history appear
in those samples. Dates, numeric fields and enum/source labels are validated.
These are cached observations, never fresh signal or delivery approvals.

## Verification and deployment boundary

Independent review covered candidate screening, enrichment budgets, reference
geometry, claim/send limits, and the bounded private collector projection.
The regression cases use synthetic data and isolated storage; actual SMTP and
external network calls are blocked. Twelve new reference-invariance cases
failed against the preceding source loaded only in memory and pass after repair.

Final frozen-source offline suite: **8,080 passed, 3 skipped** (310.35 s).
Seven additional deployment source-contract tests passed; the 64 unchanged
Bash deployment behavior cases were not executed on this Windows run.
No failing test remains in the executed suite. One pytest import-rewrite
warning concerns eager safe API initialization, not an application failure.

The private QA starter uses isolated stores, fake credentials, blocked external
network/SMTP, and normal subscriber/outbox defaults. Initial artificial
disabled flags caused fixture failures and were corrected in that starter,
not in the application. An existing auth-readiness test now supplies the
actual rejected-key sentinel with a stale False flag instead of mocking only
the obsolete cached flag; production authentication code is unchanged.

Deployment still requires the committed revision on Hetzner and subsequent
normal scans. No production email, server restart, trade or reminder activation
was performed as part of local verification.
