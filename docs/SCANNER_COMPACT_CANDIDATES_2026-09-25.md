# Compact result-row warnings

Display-only follow-up to the compact scanner status header. Previously,
`compact` only reduced typography: each result could still show twelve
warnings, mail/score explanations and a second retest panel in full.

## Changed

- Shared stock/crypto and detail surfaces show one release/context status and
  one prioritized, plain-language warning, with a pending-retest qualifier.
- Validated barrier prices can appear in the short warning. Unknown internal
  reason labels, exact distances and full checks remain in closed Details.
- Mail precheck, trade/setup scores and the daily-quality breakdown are inside
  the same closed disclosure. Details clicks do not select the stock.
- Retest evidence uses the existing status panel; the legacy evidence-backed
  fallback is preserved when no visibility annotation exists.
- Completed-daily rows retain a short dated close-price label, not a repeated
  subscription-plan explanation.
- The strategy quality column is now called Tagesqualität. Its existing 96-point
  scale is displayed correctly and no longer called an entry confirmation.

No scanner thresholds, scores, backend release rules, mail permissions,
recipient settings, reminder behavior or trading actions were changed.
Existing data-visibility attributes and candidate grade styling are preserved.
This UI patch does not repair the separately audited Cup/history or structural
confirmation problems and is not evidence of a delivered signal mail.

## Verification

- All 626 frontend tests passed.
- Independent candidate/mail/retest suite: 97 passed, with API-importing tests
  run through isolated stores, synthetic credentials and blocked external I/O.
- Bundle source/hash verification passed.
- Compiled local application checked in Chromium at desktop 1440x960 and
  mobile 390x844 with synthetic route responses; external requests blocked.
- Closed initial details, open/close interaction and no horizontal page overflow
  checked. Private QA screenshots remain under output/playwright, not in Git.
- A malformed-warning counterexample found in review was fixed: compact prices
  are taken only from the same validated, bounded warning list as the details.

Deployment is a frontend-file update followed by a hard refresh. No service
restart is required for this patch; API health revision/hash are startup values
and do not by themselves verify a subsequently pulled static frontend bundle.
