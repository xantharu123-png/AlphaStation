# Mail-chain and BI/reminder repair — 2026-09-24

## Observed production state

The private post-deployment snapshot from 08:19 UTC confirms revision
`06f772a0abfa`, but no new scanner SMTP acceptance. Its stock sweep was still
running; `mail_status=not_attempted` is not a finished failed delivery.
Momentum had ten stored candidates whose native plans were blocked; candidate
visibility is not a trade-mail authorization. The two preserved personal
reminders were triggered/disabled and ownerless, not active subscriptions.
Private exports, addresses and candidate details are not included here.

This repair addresses independently reproduced code defects. The old aggregate
snapshot does not identify how much each defect contributed to past mail silence.

## Corrected defects

### Stock swing mail

- Admission is per row, not `all(rows)`: an invalid/stale daily row or a legacy
  live row cannot silence an independent, valid completed-1D plan outside
  regular trading hours.
- Invalid daily references cannot fall back to legacy live or premarket gates.
- Mixed premarket/daily rows keep their own final validation, channel and
  message wording. Intraday-only scanners do not gain the daily exception.
- The daily-close cooldown identity is chosen **before** memory and persistent
  dedupe checks. The sender claims and records the same identity. Existing daily
  cooldowns still block repeats; unrelated regular-session cooldowns do not.
- A valid daily-plan batch no longer logs a false whole-batch market-closed skip.

### BI completed bars

- Swing parsing excludes sessions not yet available at the fixed analysis time
  before reading their OHLCV. The exchange calendar and configured data delay
  determine availability, including early closes and DST.
- Envelope counts, pagination, timestamp validity/order and future timestamps
  remain checked. No prices are imputed, repaired or silently replaced.
- Bad **completed** histories remain quarantined; an incomplete BI run does not
  publish a new final result or send automatic BI trade mail. The 17/20 rule is
  unchanged.
- Error telemetry now records finite value classes (e.g. missing, null, zero
  price) and coarse positions (first/interior/last), not payloads or tickers.
  The subtype of the earlier production `invalid_bar_value: o` is not recoverable
  from its old counters; this fix must not be claimed as proof of that subtype.

### Personal reminders

- Malformed timing/retry metadata or a delivery-preflight exception affects one
  reminder, not all later owners. Preflight retries remain bounded.
- Store read/write failures still stop the tick. A failure after the durable
  in-flight claim becomes uncertain/manual reconciliation, never a blind resend.
- Crypto reminders consume the final execution decision. A VWAP-only check
  cannot bypass a still-pending retest. Legacy explicitly rejected trigger
  payloads cannot be legitimized by a newer cache.
- No owners are guessed, no subscriptions activated and no expiry policy changed.
- Terminal delivery states are interpreted consistently despite case or
  surrounding whitespace. A stored `sent` state without a timestamp cannot
  cause a resend; no new acceptance timestamp is fabricated.

## Per-run mail evidence

Manual stock attempts and the automatic stock sweep now retain `mail_audit`:

- `candidate_rows`: rows offered to the mail helper, not approved signals.
- `reason_occurrences`: allowlisted rejection occurrences; reasons can overlap.
- `transport_events`: sender calls, complete/partial SMTP acceptance, partial
  uncertainty, unknown outcome, definite failure and outbox enqueue, separated
  into trade versus other messages.

Counts are scoped to that synchronous call/thread. Other scanner threads and
previous scans cannot contaminate them. They contain no recipient, subject,
ticker, raw error or price. One SMTP message with several recipients is not
counted as several messages. Acceptance does not prove inbox placement. A
missing audit on an old attempt is unknown, not zero. Existing `guarded` remains
explicitly an executed guard, not a delivery receipt. The standalone read-only
collector mirrors the allowlist without importing server application code.

## Validation and deployment boundary

All tests run with isolated state, external sockets denied and SMTP replaced
by deterministic fixtures. New regression suites cover mixed-row mail modes,
dedupe namespaces, incomplete daily bars, per-owner reminder isolation and
independent transport-audit adversarial cases. Existing positive and negative
signal, geometry, freshness, tracking and mail guards remain in place.

Verification results:

- Broad regression: **7,842 passed, 3 skipped** (355.68 seconds), excluding
  private `output/`, `tmp/` and the unchanged long-running
  `test_deploy_auto_update.py` suite.
- Final rerun after the terminal-state normalization correction: **485 passed**
  across worker isolation, crypto capability, daily structure reminders, email
  audit, scoped transport telemetry and stock swing mail isolation. These
  overlap the broad suite and must not be added as unique test totals.
- Independent adversarial checks reproduced mixed-batch blocking, incorrect
  cooldown lookup and terminal reminder resend cases before their corrections.
- `git diff --check` clean. Frontend and deployment scripts are unchanged.

No genuine mail, subscription, scan or production restart was triggered by this
local repair. Server deployment and a subsequent completed regular scan are
required to obtain the new per-run evidence; no test signal should be fabricated.
