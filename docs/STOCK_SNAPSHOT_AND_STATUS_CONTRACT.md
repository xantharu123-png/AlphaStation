# Stock snapshot and attempt-status contract

## Market observations stay atomic

The full-market snapshot is the first authoritative observation for a symbol.
Gainer/loser responses can add symbols and source labels, but must not overwrite
or splice its trade, quote, daily bar, previous close or timestamps. The first
observation also wins for mover-only duplicate symbols. This prevents sparse
duplicates erasing valid fields and avoids joining prices from one response to
timestamps from another.

This does not repair missing provider entitlements. The provider documents
`lastTrade` and `lastQuote` as optional, dependent on access to trades/quotes:
<https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot>.
Minute/daily aggregates are not silently substituted for live trade prices.
Existing data-validity, execution, liquidity and BI 17/20 gates are unchanged.

## Attempt status is not result evidence

The four automatic stock strategies persist an attempt independently of their
last successful result cache. The results endpoint reads this bounded diagnostic
record after restarts; it does not start scans or change cache/database state.

- Only fixed strategy filenames and a validated, bounded public projection are
  returned. Raw results, arbitrary error strings and private fields are excluded.
- A persisted `running` record is historical evidence, not proof of a live worker.
- Attempt IDs do not replace scheduler/manual acknowledgement IDs.
- An incomplete/error attempt cannot become a successful zero-result scan merely
  because a retained result cache contains zero rows.
- A newer manual acknowledgement remains authoritative. Cache-based recovery
  must belong to the requested strategy and be later than the terminal failure,
  not merely later than its start.
- UI polling follows both manual strategy and automatic sweep ownership, and
  refreshes on a new attempt even if its error code repeats unchanged.

## Verification boundaries

Regression tests cover atomic observations, error projection, chronological
recovery, orphaned attempts, private/malformed files, manual acknowledgement
identity and automatic refresh. Browser checks use local simulated API responses;
they are not provider, production scanner, SMTP, inbox or profitability evidence.
The frontend bundle is generated from `frontend/index.html` with the repository
build script and verified separately.

## Windows dedupe lock regression found by the full suite

The all-tests run exposed a pre-existing first-use race in the Windows mail
deduplication lock: initializing a byte before acquiring its lock could collide
with another process already holding that byte. The Windows CRT explicitly
allows locking beyond EOF, so an empty lock file requires no initialization
write. The fix retains exclusive locking and leaves Linux `flock`, lease
ownership, TTL and mail eligibility unchanged.

Reference: <https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170>.
This local Windows defect is not evidence explaining the Linux server's missing
market prices or its mail-delivery state.

## Final local qualification (2026-09-14)

- Integrated suite: **4,744 passed, 4 skipped**, 163 tracked root test files.
  The skips are existing Windows limitations for Linux/symlink contracts.
- Independent review covered both the stock snapshot/status changes and the
  Windows lock fix; chronological counterexamples were added before acceptance.
- Windows lock stress: 20 independent four-process runs, each exactly one claim
  accepted and three rejected. No timeout increase or lock-error suppression.
- Generated frontend bundle: `68a98078a620`; build verification passed.
- Simulated browser QA: error, repeated automatic error refresh, later completed
  result and active automatic sweep; desktop 1280x720 and mobile 390x844 checked.

This qualifies the local implementation only. No server deployment, new valid
production signal, mail receipt or improvement in realized returns is claimed.
