# BI isolation and stock mail selection repair — 2026-09-24

## Production evidence and scope

The private 12:24 UTC export shows server revision `866121604acf`, a healthy
API and a completed four-strategy stock sweep with 20 candidates. Its scoped
transport counters are empty: all 20 were rejected before the sender.
That observation is not evidence of an SMTP outage or of a lost inbox message.
It does not prove that the additional selection defect below affected those
20 candidates: they failed earlier checks.

No private exports, recipients, authentication material or real candidate rows
are part of this change. Local fixtures are synthetic and do not send mail.

## Explicitly approved BI policy change

On 24 September the user approved excluding a malformed individual stock while
allowing independently valid BI signals to proceed. This supersedes the
whole-run malformed-OHLCV policy documented in `MAIL_CHAIN_REPAIR_2026-09-24.md`.

- Retry a malformed OHLCV response once using the identical URL, parameters,
  analysis clock and completed-session cutoff. The run-wide retry budget and
  rate limiter remain in force. A changed timestamp set is not a repaired series.
- Persistent symbol-local OHLCV value/geometry errors exclude that entire
  series. Do not drop individual bars, truncate old history, interpolate prices
  or weaken the 17/20 indicator requirement.
- Final coverage is `complete_with_exclusions`, with explicit excluded/valid
  data counters. The worker is done, but complete market-data coverage is not
  claimed. API, frontend and private collector preserve this distinction.
- Only independently validated rows pass to the existing trade-plan/mail gates.
- Exclusion is scoped to this run, not a permanent ticker blacklist or a claim
  about company quality. The next scan reevaluates the same symbol if it remains
  in that scan's universe; exclusion and retry counters start afresh. The UI,
  API and cache wording explicitly say "in diesem Lauf" and "keine dauerhafte
  Sperre". A data retry is attempted only while the shared retry budget remains;
  exhaustion must not be described as a proven failure of a second request.
- Provider authentication, rate-limit, envelope, timestamp, incomplete-traversal,
  analysis and cancellation failures remain whole-run failures. Paused/stopped
  scans do not publish a final result or mail an unfinished result; explicitly
  provisional live caches remain supported.

The production error was a zero opening price at the first returned historical
bar. Its position alone does not prove its age. Longer histories remain needed
by ATR and pattern calculations, so shortening them would change the strategy.

## Stock mail selection and duplicate prevention

Previously, the top-ten cap was applied before final price/path validation.
If those ten became invalid, a valid eleventh row was never examined.

The existing maximum 50 inspected rows now form a bounded reserve. Only final
eligible rows consume the unchanged maximum ten sender-row allocations; the
stricter YELLOW limit remains two. Existing WATCH allocations share the same
budget. Failed/uncertain send attempts consume capacity, preventing a mail storm.
No new WATCH channel or subscription is enabled by this change.

The persistent 900-second claim is renewed atomically after 300 seconds of
waiting, before and after slow final validation. Renewal requires exact
ownership; an old worker cannot renew or release another worker's claim.
Unknown SMTP outcomes remain blocked. Successful cooldown timestamps use the
actual send time, not the beginning of a potentially long batch.

Existing market/breaker WATCH messages also recheck aging ownership after
rendering. Lost rows are removed from the rendered table, subject count and
dedupe keys together; a lost breaker cap prevents that message. This protects
existing operator-selected channels and does not create candidate subscriptions.

## Score and UI consistency

- Repeated decoration previously reduced a stable derived score (92 to 91 in
  the regression). Display processing now preserves the exact original producer
  score/grade/action fields. It does not restore uncapped legacy `raw_score`.
- Shared stock candidate panels distinguish producer/setup score from the
  actual mail/trade-plan score, explain rejection reasons and separately state
  that this preview is neither a send permission nor a delivery receipt.
- Momentum quality now exposes its exact components and deductions. The
  reproduced 70/96 result names the missing eight points to the unchanged
  78-point floor. Positive descriptions can no longer hide the shortfall.
- Daily quality, daily extension and reference-candle target-touch reasons
  are named as daily observations, not as missing future continuation or retest.
- Missing/malformed scores or unknown quality schemas never imply a passed
  check. Display metadata cannot grant execution permission.
- Native expandable explanations do not also trigger the table's ticker-detail
  click handler. The same panel is used by shared scanner/detail surfaces.

The 17/20 rule, native trade geometry, R:R, daily freshness, retest-independent
confirmed-breakout policy and valid-input quality thresholds are unchanged.
Candidate visibility does not subscribe the user to candidate email.

## Verification of the original repair (`5835794`)

Focused and adversarial tests cover selection reserves, regime quotas, ownership
renewal, failed/uncertain delivery, idempotent display, exact quality arithmetic,
retry budgets, isolated data exclusion, fatal global failures, pause/resume,
collector allowlists and new zero-result coverage semantics.

Browser QA uses the shipped frontend locally with synthetic API responses and
all external requests and non-GET API writes blocked. Desktop 1440x1100 and
mobile 390x844 were inspected. No horizontal page overflow or browser error was
observed. This is UI evidence, not a production-provider or SMTP test.

Final frozen-source offline run: **8,002 passed, 3 skipped** in 432.56 seconds.
The command excluded `output`, `tmp` and the unchanged
`test_deploy_auto_update.py` Bash behavior suite. Seven static tests from that
deployment file passed separately; 64 Bash behavior cases were not run here.
Ten additional collector regressions added after broad-suite collection passed
separately. The final combined mail/visibility/collector subset passed 112 tests
(overlapping the counts above, not an additional unique total).
That repair's frontend source fingerprint was `4f7e38dbe0b4`.

## Follow-up: exclusion is per scan, not permanent

Status messages now explicitly describe an exclusion in the current run and
reevaluation in the next scan, without changing scanner or mail eligibility.
Six new regression cases execute consecutive real worker calls in the same
process with the same cache and universe: Long/Short, recovered 17/20 admission,
continued 16/20 rejection, and a fresh retry budget after prior exhaustion.
The targeted offline BI/API/frontend/collector suite passed **826 tests**.
The frontend was rebuilt with source fingerprint `68073d422b21`. The original
8,002-test run above was not repeated for this wording/test-only follow-up.

Deployment needs the new commit on Hetzner followed by completed normal scans
and the existing per-run/journal evidence. No actual mail, server update,
reminder activation or owner assignment was performed during local testing.
