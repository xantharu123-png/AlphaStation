# Daily-signal mail eligibility repair — 2026-09-26

## Observed failure and scope

A private post-update export on `75dad91` records a completed four-strategy
stock sweep: 17 Momentum, seven Gap Long, six Gap Short, and zero Cup rows.
All 30 candidates reached the mail guard; no transport event was recorded.
This specific round stopped **before SMTP**, not in a pending mail queue.
The native-plan stage reported 25 first-barrier/R:R rejections and five
unconfirmed crossed-level rejections. These are builder-stage counts, not
30 independently established root causes or a prediction of repaired sends.

The recent SMTP rejection fixes are already present in that revision and are
retained. No account, recipient, subscription, outbox, dedupe or acceptance
ledger is reset by this change. Private source exports are not committed.

## Reproduced contradiction

The completed-daily scanner excludes the signal session from its historical
breakout and volume baseline. The canonical plan nevertheless used that same
session's high/low as **previous-day** references. A close near its own high
therefore encountered a new first opposing zone at the signal close. The
result could be an overlapping zero-room barrier, a score cap of 45, and no
mail even when the preceding resistance had a valid completed-close break.
SHORT has the mirrored problem at its own low. New session evidence could
also change the membership/confirmation date of older crossed clusters.

The repaired contract explicitly distinguishes two clocks:

- A completed-daily **signal plan** uses session-reference levels established
  before its signal session opened. All completed signal bars remain in the
  separate pivot and breakout-confirmation stream.
- An ordinary **live/chart** snapshot continues to use the latest completed
  session's extrema. A former signal's high/low becomes a genuine previous-day
  reference for a subsequent day's signal; it is not permanently exempted.

This deliberately supersedes the blanket latest-session wording in
`MAIL_SELECTION_AND_REFERENCE_ZONES_2026-09-25.md` **only for explicitly bound
daily-signal snapshots**. It does not delete session highs/lows globally,
skip a real preceding barrier, weaken the first-barrier 1.35R check, or lower
mail R:R, quality, liquidity, data-freshness or BI 17/20 requirements.

## Implementation and negative cases

`build_structure_snapshot(session_reference_before=...)` filters only the
session-reference prefix. Other evidence, ATR, completed-bar counts, and
break/retest proofs still use their original causal data. A future reference
boundary is rejected. An explicit quality flag identifies this reference mode.

Only the completed-daily stock-strategy path opts in. Its adapter checks the
analysis close, last completed history bar and bound reference price. A
mismatched session, price, clock, missing signal bar or invalid date yields no
usable snapshot. Live/chart callers, Biotech's separate live enrichment, BI's
separate plan and crypto retain their existing defaults.

Independent review reproduced another adapter issue: explicit unfinished
flags could be discarded while attaching exchange timestamps. The daily
adapter now rejects any negative `is_closed`, `complete`, `completed` or
`final` flag for structural consumers, including conflicting flag aliases.
The diagnostic adapter shared by Wyckoff and Elliott explicitly preserves
these rows with their exchange clocks and canonical `is_closed=False`;
downstream completed-bar normalization still excludes them from evidence.

No future or unfinished candle can confirm a breakout. Missing retests alone
remain warnings for an otherwise confirmed break. Real nearby previous
barriers still block. An absent structural TP1 still blocks mail: this fix
does not promote projection-only targets just to manufacture eligibility.

## Verification

The new mirrored end-to-end fixtures use real OHLCV, scanning, native levels,
classification, final validation, sender handling and the delivery journal.
Only external providers and SMTP transport are replaced; all state is isolated
and real network/mail is forbidden. An independent older, confirmed target
is provided, so positive cases do not rely on projected TP1 values.

Before repair, the own-wick cases failed with `WAIT_BREAK_RECLAIM`, score 45
and zero SMTP calls. After repair they produce one simulated accepted **trade**
mail each, with `fill_evidence_verified=0`. Mirrored genuine older nearby
barriers remain rejected. The earlier target-risk fixture now explicitly
supplies an older confirmed barrier instead of implicitly testing the bug.

Adapter-level tests separately reproduced eight explicit-incomplete cases
before their repair. Additional tests cover live default behavior, historical
barriers, the following signal day, prior weekly references and binding errors.

The full Windows run also exposed two timing assumptions in unchanged
suppression-telemetry tests. The atomic-counter test now uses a test-only lock
wait allowance; a separate deterministic busy-lock test verifies that the
production 350 ms budget and dropped-write accounting remain unchanged. The
process-death test allows 30 seconds for Windows interpreter/API import and
cleans up its own child on failure; it still requires successful process exit
and actual subsequent writer access. No telemetry production code was changed.

Final frozen-source Windows regression: **8,927 passed** in 354.51 seconds,
with one pytest warning about the deliberately eager `anyio` import. The
isolated runner blocked external network/SMTP and used new private test state.
The unchanged Linux/Bash-specific `test_deploy_auto_update.py` and
`test_deploy_migration.py` modules were not run on Windows; this is not a
claim that their server deployment checks were executed.

Independent review approved the signal-session boundary, LONG/SHORT negative
cases, completion-flag handling and test-only timing adjustments. The final
run includes the mirrored native-plan-to-mail/journal tests and the restored
Wyckoff adapter contract; earlier intermediate failing runs are not used as
release evidence. `git diff --check` is clean.

## Deployment boundary

Stock strategy cache generation advances from 12 to 13; old plans must be
recomputed by a fresh successful scan, not relabelled or replayed as fresh
mail candidates. Existing reminders and delivery claims are untouched.

This local verification does not establish how many current production rows
will pass all remaining gates, nor receipt in a real inbox. Deployment and a
subsequent completed production scan are distinct checks. No real testmail,
trade, credential change or server restart was performed in local QA.
