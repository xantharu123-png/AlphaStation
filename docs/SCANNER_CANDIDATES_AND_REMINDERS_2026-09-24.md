# Scanner candidates, timeframe proof and personal reminders — 2026-09-24

## Accepted product contract

The user explicitly requested the timeframe defect to be fixed first, then
scanner candidates with visible warnings and personal retest notifications.
This supersedes earlier UI-only instructions to hide every waiting candidate.
It does **not** supersede primary scanner, mail, tracking or execution rules.

- Show real scanner candidates with their actual rejection/waiting reason.
  Examples: nearby opposing level, unconfirmed breakout, missing native plan.
- Show a known barrier price, distance in percent and R, and timeframe when
  those measurements are present. Missing values remain missing.
- Separate released signals, warning candidates and context/position rows in
  counts and labels. A high score or grade cannot turn a warning green.
- BI remains 17/20 with its hard contraindications. Cup, Wyckoff and momentum
  retain their primary pattern contracts. Invalid prices, bad OHLC and stale
  daily references are not promoted to candidates. This is not a list of every
  rejected symbol in the universe.
- Presentation does not set trade permission, generate synthetic plans, send
  scanner trade mail, create tracked trades or activate a broker action.

## 1. Correct the multi-timeframe defect

`modules/level_zones.py` now filters proof freshness **before** ranking competing
break/retest witnesses. A stale 4H witness can no longer displace a still-current
1D witness and then cause a false downstream rejection. Each proof uses the
existing two-source-bar age limit plus two seconds of clock skew; the rule is
not weakened. If all proofs are stale the structure remains pending, not
confirmed. Later opposite closes and conflicting evidence still invalidate it.

Regression tests cover long/short, current/stale combinations, age boundaries,
all-stale evidence and retained invalidations. Earlier fixtures that expected
an old proof to remain current were moved to actual completed-bar cutoffs.

## 2. Presentation across scanners

`modules/scanner_visibility.py` copies rows and supplies `visibility_status`,
`visibility_label`, `visibility_warnings` and `visibility_is_trade_signal`.
Canonical trade fields are not changed. API response decoration covers generic
stock strategies, BI, Bear, Biotech, volume context, ORB, Penny, Early Movers,
Crypto Explosion, BTC divergence, New Listing and combined crypto results.
Nested instrument lists are counted without duplicating ORB convenience lists.

The shared frontend warning component is used in tables and details. Native
plan failure is not concealed by a green fallback plan. Snapshot propagation
preserves `data_quality` so the headline counts describe the same rows as the
table. Warning candidates are not renamed as trade signals in completion toasts.

## 3. Personal daily structure reminders

The new `structure_1d` mode is an opt-in **stock swing** notification. The user
opens a supported candidate, selects a confirmed daily breakout or retest, and
chooses 7, 14 or 30 days. No mass subscription is created.

- The server resolves symbol, scanner, direction and the zone from its own
  current scanner cache. Browser-submitted prices cannot define the trigger.
- A frozen causal zone is monitored using completed 1D exchange sessions,
  including half-days and the configured 15-minute data delay.
- A breakout needs a directional close. A retest additionally needs a later
  candle touching the zone and closing on the holding side. A same-candle
  breakout/retest, simple proximity or a client confirmation flag is insufficient.
- Only events after reminder creation trigger a new notification. Later
  invalidation is terminal. Missing intervening sessions, stale data and
  explicitly unfinished candles do not create confirmations.
- One evaluation failure does not stop the other reminder evaluations.
- The browser and email are personal structure notifications, not new scanner
  recommendations or tracked trades. Admin polling uses `personal_only=true`.
- Missing/deleted owners and disabled email preferences prevent sending.

The older crypto intraday reminder path is not a new 1D crypto monitor. Do not
interpret this extension as audited Short/Continuation support for every crypto
scanner. Its supported boundary is Early-Mover Long with a verified server
cache source, valid perpetual venue metadata and finite Long plan geometry.
Unsupported source/direction/continuation combinations are rejected by the API,
not merely hidden in the UI. Legacy triggered retry payloads are checked too;
a current valid Long cache must not legitimize a saved Short email payload.
Saved fallback is allowed only for snapshots originally bound by the API.

## Persistence and delivery

Reminders now use `ALPHA_DATA_DIR/trade_reminders.json`. Writes are atomic and
flushed; a failed write is not acknowledged as a successful subscription.
The delivery attempt is persisted before SMTP. Uncertain in-flight delivery
after a crash requires reconciliation rather than an automatic duplicate.
If the outbox accepts the message it alone owns retries; queued is not sent.
The existing SMTP/delivery gates remain active.

### Deployment prerequisite

The former file was `/tmp/alphastation_trade_reminders.json`, possibly inside
the API service's private `/tmp`. Do **not** blindly restart the old service
before checking for active reminders there. A new process cannot migrate a
file that systemd has already removed. Copying a live file and later restarting
also risks copying an outdated delivery state. Migration must preserve the
latest state under quiescence, or wait for existing reminders to finish.

An ordinary source push is not this migration and is not server deployment.
See the dedicated reminder pre-deploy check/instructions delivered with this
change. No server, scheduler, trading or SMTP action was performed during the
local implementation and mock-browser checks.

## Validation

Tests run with isolated data/auth/runtime directories, external sockets denied
and SMTP denied. Private real-server evidence and browser fixtures stay under
ignored `output/`; they are not included in the source commit.

Browser checks use loopback fixtures, including a warning candidate without a
native plan and a mocked personal reminder request. They verify presentation
and request wiring, not delivery to a real inbox or the deployed service.

Final regression on frozen source: **7,768 passed, 4 skipped**, split into
non-overlapping cohorts:

- Core suite, excluding the two separately checked files: 7,639 passed,
  3 skipped (363.89 seconds).
- `test_reminder_deploy_preflight.py`: 59 passed. Includes anonymized inventory
  of legacy orphan/duplicate identities without treating them as deploy-safe.
- Unchanged `test_deploy_auto_update.py`: 70 passed, 1 skipped (648.13 seconds).

The full run caught an Early-Mover display regression: the old confirmed
execution-quality alternative had been omitted from the candidate branch.
It was restored without modifying the original regression fixture. Independent
review also reproduced and closed legacy crypto reminder state-alias and
retry-payload bypasses. The final core suite includes their regressions.

Generated frontend bundle verified: `f5ad8b0b3fca`. Desktop and mobile checks
used the loopback warning/reminder fixture, not a production notification.
No real SMTP message or server mutation was performed. Production deployment
and acceptance of an actual signal/reminder message remain separate checks.
