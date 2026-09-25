# Scanner state consistency repair — 2026-09-25

## Scope

Repair reproduced contradictions and investigate adjacent false rejection and
false approval paths. No lowering of BI's 17/20 contract, pattern requirements,
R:R, liquidity, daily quality or final mail-score thresholds. No live provider
query, scan, SMTP message, trade or server update was performed for these tests.

## Findings and corrections

### 1. A missing retest did not prove a confirmed breakout

`scanner_visibility.reasons` previously generated a confirmed-break label from
`retest_status=not_confirmed` alone. It could simultaneously report a missing
closing-price confirmation. The derived warning now requires the existing
complete four-field producer contract in one coherent container. It never
combines partial row and nested-plan fields. Explicit failed, expired,
invalidated, unconfirmed or opposite-direction evidence prevents that claim.
Completed-retest evidence cannot simultaneously be described as pending.
Explicit invalidation or conflicting directions also downgrade the display's
release flag; merely hiding the incorrect explanation would not suffice.

The frontend no longer reintroduces stale raw confirmation text inside the
closed Details panel after the backend supplied a contradictory current reason.
The compact layout and separate daily-candle-quality rubric remain unchanged.

### 2. The opposite zone edge could erase a genuine break

The previous full-geometry anchor required both present bounds to have existed
before the confirming close. An unchanged LONG resistance edge could lose its
proof when new support extended only the lower edge; SHORT was symmetric.

Directional anchors now retain a known breakout edge while the causal role and
continuously connected structural evidence persist. A changed breakout edge,
new disconnected component, late bridge, newly appearing direction, future bar
or failed latest close cannot borrow earlier proof. The full zone and its stop
edge still carry the latest membership timestamp; stop distances are not frozen.

The independently reviewed implementation exposed a related trap: widening the
zone could retrospectively widen retest tolerance. The causal anchor now also
retains its original width, preventing a previously missed touch from becoming
a historical retest. Real retests remain valid. New evidence uses the explicit
`connected_role_boundary_v2` model; old `connected_role_geometry_v1` certificates
remain readable only with the existing exact zone/bounds/time binding.

### 3. Historical retests could override a subsequently lost boundary

The final barrier-evidence validator checked current side/price for breakout-only
certificates but not for older RECLAIMED certificates. It now rejects an observed
price back inside the zone for both models. Conflicting explicit direction fields
or observed-price aliases cannot hide behind precedence. Rounded prices may
differ numerically but all must remain valid and on the confirmed side.

A completed daily reference close remains a valid observation for 1D swing;
this does not introduce a requirement for live quotes. Entry/target prices are
not treated as fresh observed prices. Price-less legacy evidence remains readable
as historical evidence, not proof of a new market quote.
Boolean values are not market prices. Nonfinite or nonpositive certificate
prices and malformed bar counters are rejected before they can bypass numerical
comparisons; valid legacy numeric price strings remain supported.

### 4. One stock's history could terminate a Cup/stock leaf

Strict stock scans now use a typed daily-aggregate adapter rather than a
best-effort chart adapter that collapsed empty histories and provider failures.
An explicitly successful empty result is an insufficient-history exclusion.
An invalid OHLCV series receives at most one identical-query retry, bounded by a
shared 20-retry budget. Recovery requires identical observation timestamps; no
bad bar is repaired, dropped, or filled. If the whole symbol remains invalid,
it is counted and excluded while unrelated valid symbols continue. More than
20 invalid symbols in one leaf aborts as a possible systemic data incident.

Auth, quota, transport, malformed response envelope and timestamp failures still
fail the leaf visibly. Completed runs with invalid-symbol exclusions retain
`complete_with_exclusions` through persistence and read-only export. Successful
sibling strategies can still reach their mail checks if another leaf fails.
The collector exports only allowlisted numeric/error categories, never symbols,
provider bodies, URLs, credentials or recipients from these diagnostics.

## What was deliberately not changed

- A strong daily candle and a nearby resistance are not inherently contradictory.
- A candidate is not automatically a mail-approved trade.
- Genuine historical and session extrema remain subject to the established
  first-barrier and R:R policy. Signal-session extrema are not silently exempted.
- The latest available private export showed successful stock siblings reaching
  eligibility checks, but no sender invocation in that sweep. This repair does
  not constitute an actual SMTP acceptance or inbox-delivery observation.
- No actual provider cause is invented for the old Cup outage: the historical
  best-effort adapter did not preserve enough evidence to distinguish it.

## Verification

Focused regression coverage includes Long/Short symmetry, unchanged versus new
breakout boundaries, causal retest width, late bridges, simultaneous membership,
future/failed bars, row/plan disagreement, stale frontend raw labels, legacy/new
certificate binding, typed history errors, retry identity/budgets, completed
exclusion persistence, native-plan/mail integration and private export allowlists.

The compiled frontend was opened in an isolated real browser with mocked APIs:
the unconfirmed fixture displayed only the missing-close explanation even after
opening Details; valid confirmed-break fixtures retained their retest warning.
No console errors occurred.

The broad regression run uses isolated disposable databases/runtime paths, fake
credentials and blocked external network/SMTP. Two unchanged Bash deployment
integration files (`test_deploy_auto_update.py`, `test_deploy_migration.py`) are
excluded from this Windows run; this is not a live deployment verification.
The frontend bundle verifier confirms fingerprint `1366dccd0be0`.

A final legacy-fixture audit found a hand-written Early-Movers certificate
missing `retest_observed`. The canonical producer has emitted this field since
its initial version (`82f5463`). The fixture was aligned with that producer;
missing/malformed optional and required retest flags remain rejected. The
production validator was not weakened to make this fixture pass.

Final frozen-tree regression result: **8,655 passed**, zero failures, in
248.58 seconds. One pytest warning notes that the eagerly imported `anyio`
module cannot have assertions rewritten; it is unrelated to product behavior.
The changed areas were also reviewed independently and exercised in targeted
Long/Short, native-plan/mail, browser and typed-provider regressions.
`git diff --check` and `scripts/verify_frontend_bundle.py` pass. No private
exports or QA runtime artifacts are included in the publication scope.

## Deployment boundary

The implementation and local regression checks do not update Hetzner. After
deployment, a completed fresh scan is required to assess the corrected causal
zone history and new provider diagnostics. Actual signal-mail acceptance and
inbox arrival still require live delivery evidence; local mocked tests do not
establish either.
