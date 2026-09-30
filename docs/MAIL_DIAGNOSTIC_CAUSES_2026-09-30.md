# Mail diagnostics and precise plan rejection causes

## Scope

This continuation repairs operator diagnostics and misleading rejection labels.
It does not lower scanner, score, R:R, structure or mail requirements. BI still
requires 17/20. No real message, scan, order, server restart, credential change
or installation migration was initiated during this work.

## Reproduced defects and corrections

### Repeated reference-universe filtering

The read-only admin audit rebuilt the common-stock reference set for every
candidate. An offline reproduction with two 40-row caches and 6,000 reference
stocks performed 480,080 name checks in 15.887 seconds. Request-local reuse
reduced this to 6,080 checks in 0.292 seconds on the same local machine.
These timings are a local comparison, not a server response-time guarantee.

Only the synchronous `/api/email-alert-audit` request opts into this scope.
Context variables separate concurrent requests; nested calls restore their
parent scope and exceptions always clean up. Failed reads are not cached.
Other readers and senders continue to revalidate. This is not a process cache
and does not claim transaction consistency across independently written files.

### Signal and crash-warning counts

The table used the signal-only count, but its description added crash warnings
to that count. It could therefore show zero alongside "one candidate passed".
The admin table now displays separate signal and crash-warning counts. Neither
means a message has been sent. Mobile layout allows horizontal table scrolling.

### Hidden transport failures and time labels

Known sanitized sender results such as `SMTPAuthenticationError:not_delivered`
were previously displayed as an unclassified reason. Exact type/outcome codes
now distinguish authentication, recipient, sender, DATA rejection, general
failure and unknown delivery outcome. Unknown DATA outcomes remain quarantined;
this patch does not retry them. Raw subjects, exception text, credentials and
recipients are not exposed by the diagnostic mapping.

Several known operational skip codes, including disabled crypto watch mails,
also receive explicit operator labels. The response timestamp is explicitly
UTC. An unassigned next scanner time says "noch nicht festgelegt", not "nie".

### Valid arithmetic incorrectly labelled low R:R

The shared plan gate returned a boolean for several unrelated checks. Both
ordinary and crash-signal classification converted every negative result into
`trade_rr_below_threshold`, including missing structure, projection-only targets
and unconfirmed break/reclaim evidence.

An offline long/short mirror case has entry 14.82, risk 0.76 and target rewards
1.22 / 1.88: TP1 is about 1.6053R, TP2 2.4737R, weighted plan R:R 2.0395R.
Its numerical checks pass. If TP1 is only a projection, the correct reason is
"Kursziel nicht durch Struktur bestaetigt", not low R:R. Six regressions first
reproduced that contradiction through the real candidate classifier.

The plan predicate now delegates to one reason-producing implementation:

- `trade_target_not_structural`: projection-only or weak target evidence.
- `trade_structure_not_confirmed`: rejected or unavailable structure.
- `trade_breakout_not_confirmed`: missing required break/reclaim evidence.
- `trade_target_quality_invalid`: target spacing/allocation requirements.
- `trade_rr_below_threshold`: the numerical reward/risk requirements.

The boolean admission result is unchanged. All newly distinguished reasons
retain the previous NO_TRADE decision. Finalized top-level structure fields
still override stale nested data. Reviewed reason allowlists in suppression
telemetry and the standalone evidence exporter are synchronized. The compact
scanner warning uses the same distinction.

## Verification

- 400 focused API, structure, scanner-runtime, suppression and frontend tests
  passed after the plan-reason correction.
- 370 exporter, admin-route, privacy, request-scope, count and UI tests passed
  after synchronizing the export allowlist.
- A separate offline comparison evaluated the exact deployed predicate from
  `716f2eb` against the repaired predicate in 2,880 long/short combinations:
  accepted/rejected outcomes matched for every combination. Variants include
  missing levels, invalid stops, close targets, structure/target flags,
  break/reclaim gates and two numerical R:R thresholds.
- Browser inspection used an isolated local fixture with writes disabled;
  desktop and 390-pixel-wide layouts were checked. No production settings changed.
- A full run performed during source edits was discarded as an acceptance run:
  its source-inspection failures disappeared on the unchanged-code repeat.
- A following run exposed the missing exporter allowlist entries and was
  interrupted; the entries were added rather than weakening its assertion.
- Final frozen full suite: **9,704 passed, five skipped**, one existing `anyio`
  warning, in **880.59 seconds**. SHA256 values for all 13 changed production
  and test files matched before and after the complete run. The frontend bundle
  is `2ebd16934df3`; its JavaScript syntax check also passed.

The full checkout also contains inherited, separate deployment-script removal
work. That work is neither overwritten nor bundled into this mail repair.

The standard offline harness uses disposable state, fake credentials and
blocked external network/SMTP. It emits the existing eager-`anyio` pytest warning.

## Deployment boundary

Production was verified healthy on `716f2ebd1e0c` during this continuation.
That is the earlier Biotech/startup correction, not this new diagnostic patch.
The next account continuation verified `d06b8af58f35` live at 17:49 UTC on
30 September: the operator has now deployed this diagnostic correction too.
Successful code tests are not a production SMTP acceptance or an inbox receipt.
Existing stored candidate annotations may retain their old reasons until a
new scan or normal result re-evaluation. No old trading mail is replayed.

A separate BI progress-display gap was identified during live inspection:
the status response omits the progress file's time/run binding, and the UI
correctly refuses to treat unbound counters as current. Partial result caches
are written only after a hit, so they cannot provide progress for a no-hit run.
Repairing that contract requires run-bound progress through producer, API and
UI; it is recorded separately in the local TODO, not patched during the frozen
mail acceptance run. A running status alone is not a completed scan.
The subsequent local repair and its separate live-delivery boundary are in
[BI progress tied to the current scan](BI_PROGRESS_RUN_BINDING_2026-09-30.md).
