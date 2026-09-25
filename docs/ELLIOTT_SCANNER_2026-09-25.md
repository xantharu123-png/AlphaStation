# Elliott scanner and related cleanup — 2026-09-25

## Implemented scope

`Elliott Wave Muster` is a separate, manual stock scanner. It uses completed
daily sessions (1D), the strict provider's 1095-calendar-day history request
(at most 800 bars), common-stock membership,
a $5–$100,000 price range and at least $1 million session dollar volume.
It does not require a momentum-day percentage or relative-volume spike.

Supported geometric families, mirrored for upward and downward structures:

| Family | Main labels | Expected smaller-degree segmentation |
| --- | --- | --- |
| Impulse | 1–5 | 5–3–5–3–5 |
| Zigzag | A–B–C | 5–3–5 |
| Regular / expanded flat | A–B–C | 3–3–5 |
| Contracting triangle | A–B–C–D–E | 3–3–3–3–3 |

The implementation distinguishes geometric counts from counts with one
independently observed subdivision degree. Unverified smaller pivots are named
P0, P1, etc.; they are not relabelled as verified Elliott waves. Multiple counts
remain selectable. Direction describes the observed structure, not a forecast.
Fibonacci ratios are descriptive, not substitutes for the hard topology rules.
Wave-length comparisons use absolute price distances, not percentage returns.

Source references: [Impulse](https://www.elliottwave.com/waveopedia/impulse/),
[corrective waves](https://www.elliottwave.com/waveopedia/corrective-waves/).
Fixed fractal scales and numeric thresholds are this application's finite
recognition model, not universal Elliott rules.

## Evidence and operational contract

- Actual OHLCV pivots with separate observed and confirmation dates; unfinished
  candles and future evidence cannot confirm a count.
- Bounded core: at most 800 bars, pivot radii 3/5/8, minor radius 1, eight
  alternatives, and a maximum pattern age of 120 bars.
- Snapshot and historical close must match the same completed session. Stale or
  inconsistent symbol evidence is excluded and counted; systemic errors remain
  errors. Existing limited invalid-history isolation is reused.
- Existing shared stock-engine ownership, cooperative pause/resume and the
  20-minute leaf budget apply. A timeout is not a completed zero-result scan.
- Partial results are published during a run; final output is limited to 100
  rows, ordered by pattern confirmation recency and liquidity.
- No automatic schedule, mail, trade tracking, structure reminder or trade-plan
  admission is added. Explicit context guards also cover forged trade fields.

## UI and related corrections

The new list shows ticker, family, structural direction, date and subdivision
status. Its own chart uses 1D, dated main-wave anchors, selectable alternative
counts and optional smaller-degree lines. Price/date/ticker mismatches prevent
chart projection. Detailed rules and anchor tables remain collapsed.

Wyckoff evidence is shown only for a Wyckoff selection or when explicitly
enabled in the chart. Its long explanation is collapsed by default. It is not
an Elliott count and is not included in the dedicated Elliott sidebar. Leaving
the Wyckoff scanner clears its automatically enabled overlay; explicit pattern
choices between other scanners remain intact.

API and background mail adapters no longer fabricate missing Entry, Stop, TP1
or TP2. Existing supplied values are retained individually. A missing value
stays missing and does not become a misleading estimated plan. Recognized
support/resistance evidence remains attached when an entry trigger is absent.
The structural entry gate itself is preserved: an unconfirmed crossed barrier
must not be promoted into a valid support/stop simply to fill a plan.

## Audit and verification

Independent review found and closed four concrete issues:

1. Ambiguous outside bars could be ignored when confirming minor subdivisions.
2. Stored minor-wave labels were not fully canonicalized by validation.
3. A snapshot and history could supply different prices for the same session.
4. Stored patterns could evade the core's 120-bar age restriction.

The full regression also caught and corrected an optional-tracker startup
dependency and the diagnosis exporter's fixed strategy-list contract. The
shared context guard is now dependency-free. The exporter reads Elliott's
bounded status/count fields, not ticker rows, wave anchors or trade plans.

Regression tests cover causal prefixes, symmetry, family topology, invalid
OHLCV, cache contracts, forged context promotion, strict partial-plan handling,
API integration and execution of frontend projection/component functions.
Playwright checks use a synthetic local fixture with external traffic blocked;
desktop/mobile charts, count selection and optional subwaves were inspected.
The mobile check exposed and fixed a viewport/scrollbar width clipping issue.

A warmed local Windows microbenchmark (20 runs per synthetic case, 240–284
bars, analysis plus validation) measured median 8.01–10.74 ms per symbol and a
12.21 ms maximum. This does not measure network requests or Hetzner throughput.

Initial release broad regression: **8,811 passed** in 405.58 seconds. The sole warning was
the existing pytest/anyio import-rewrite warning, not a failed test. Frontend
bundle validation passed (`f0e92e99247e`). Publication revision is recorded in
the delivery message. The broad Windows run uses isolated fake credentials/state and blocked
external network/SMTP. Linux-specific `test_deploy_auto_update.py` and
`test_deploy_migration.py` are excluded from that local run; this task does not
claim their platform verification. Private data exports and browser fixtures
are not release artifacts.

## Follow-up release audit repair

- Stored observations and confirmations now share one monotonic bar-index
  clock across main waves, smaller waves, alternate counts and the last bar.
  A radius-three pivot cannot claim confirmation before an already known
  intervening bar. Shared main-wave endpoints retain their main radius; no
  calendar-day spacing is assumed.
- Session expiry is separate from cache-file age. After a new completed daily
  session becomes available, even a two-minute-old result is marked stale.
  This also applies to a previously empty result and unverifiable session
  metadata. Old rows are withheld without overwriting historical scan counts
  or inventing a failed scan.
- The compact status and empty results panel both request a new scan instead
  of claiming no patterns were found. Polling does not announce a successful
  zero-match completion. Actual failed/incomplete attempts take precedence.
- The catalog's history-days description now matches the already-used strict
  provider horizon. This does not change the provider request or chart window.

Regression cases cover early/late/contradictory confirmation clocks, irregular
session spacing, rollover with one or zero patterns, missing/bad/future session
metadata, polling notifications and the rendered empty-state component.
Independent follow-up review reproduced an incomplete-attempt precedence case;
that case is fixed and tested. The review then passed 252 targeted tests and
300 generated reports (2,138 patterns), with no remaining important findings.
The core implementation was also checked against 600 generated causal reports
(4,426 pattern observations) without changed historical anchors.

Local Playwright verification on `127.0.0.1:8776` used synthetic API responses
with outside traffic blocked. Desktop 1365×900 and mobile 390×844 showed the
same short stale status in both summary and empty list, with no false zero
count. Opening the collapsed details worked. There were no console errors;
the existing locally served Tailwind runtime emitted its production-use warning.
This browser check concerns status handling, not a live provider scan.

Final frozen-code follow-up regression: **8,841 passed** in 363.29 seconds,
with the same existing AnyIO warning and Windows platform exclusions described
above. Frontend source/bundle verification passed (`5a6ce5ba82a5`).

## Explicit remaining boundaries

Not implemented: diagonals, truncated impulses, running flats, expanding/running
triangles, throwovers, complex W–X–Y combinations, extended subdivision counts
and recursive verification beyond one smaller degree. Crypto is not included.
This release is a pattern scanner, not a new executable Elliott strategy.

No Hetzner update, live provider-universe run or real email delivery test was
performed in this task. Existing production mail delivery issues are not
declared resolved by these local changes.

A read-only production health check during this task returned `healthy` on
revision `1327f9a9a579` with bundle `1366dccd0be0`; this is the pre-change server
revision, not evidence that the new Elliott implementation is deployed.
