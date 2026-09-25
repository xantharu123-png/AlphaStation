# Compact scanner status

## User-facing change

The shared strategy/BI evidence panel now shows a short status and, for daily
swing data, the 1D closing-price date. Universe counts, prefilters, special
checks, raw timestamps and technical diagnostics remain available in a native
`details` disclosure, closed by default. Empty desktop/mobile lists use the
same short status instead of repeating the long diagnostic explanation.

Failures, incomplete scans, unverified zero results, stale results, old results
displayed during a new run, live partials and rejected starts remain distinct.
Candidate warnings and market context are not labeled as approved signals.
BI evidence without a valid 17/20 contract remains visibly flagged. The compact
zero result means no released signals, not no tradable assets in the market.
The existing detailed evidence/state logic is unchanged and remains accessible.

No scanner thresholds, backend handlers, mail policy or stored records changed.

## Verification

- 596 frontend tests passed, including new compact-state and rendered-tree
  tests which exclude closed disclosure children when checking visible text.
- Compiled bundle verified: `606259a9cfcd`.
- Independent UX/source audit completed without an open blocker.
- Playwright CLI with the actual compiled frontend and synthetic localhost
  responses: completed Cup result, disclosure click/keyboard toggle, failed
  prior run and rejected start checked. Desktop 1440x900 and mobile 390x844
  screenshots inspected. No mobile horizontal overflow or console errors.
- Only the existing Tailwind development warning was observed. External
  requests were blocked; no production scan or email was sent.

At verification, public production health reported revision `847daaaec153`
and bundle `d80cffee2486`. This change is static frontend only. Once pulled,
the browser needs a hard reload; no service restart is required for this diff.
No live deployment was performed during this work.
