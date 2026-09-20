# Wyckoff: audit and causal recognition contract

Historical v1 audit. For the subsequent same-day range/context corrections,
model migration and inspectable chart evidence, see
[Wyckoff v2](WYCKOFF_V2_2026-09-20.md). The old test totals below refer only to
the v1 verification; they are not evidence for later revisions or deployment.

## Scope and deployment boundary

This change concerns the two public stock Wyckoff strategies, their chart
recognition, the legacy standalone scanner and the old accumulation display.
BI's 17/20 rule, other strategy thresholds, trade execution and automatic
scheduling are not changed. Wyckoff remains a manual stock strategy. A Cup
control scan was started by the operator on 2026-09-20; its successful completion
has not yet been independently verified. This document is not a server-update
or profitability certificate.

## Reproduced defects in the previous implementation

| Path | Reproduction | Correction |
| --- | --- | --- |
| Daily strategy | Flat prices and falling volume accepted both accumulation and distribution; 30-bar additive score, no ordered event proof | Shared event engine; minimum 60 completed bars, 180 requested |
| Standalone scanner | Zero-volume excursion counted as low-volume Spring; ATR received response dictionary rather than bars | Real-volume evidence and actual OHLC bars |
| Chart | A LONG setup at 120 could target 112.5 and 120; latest price alone promoted phase D/E | Valid directional geometry, no phase E inferred, closed-bar confirmation |
| Accumulation display | OHLC spread impersonated volume/OBV and produced 93/STRONG BUY without a Spring | Explicit unavailable score/phase when required volume and time context are absent |
| Pattern selection | Separate scanner/chart definitions disagreed; arbitrary best-direction selection | One versioned model shared across consumers, ambiguity blocks trade readiness |
| Public scanner | Top-220 post-filter cap and raw partial previews without Wyckoff proof | Recognition before costly plan generation; all surviving candidates checked; proof at result/mail boundaries |

These are deterministic defect reproductions, not an attribution of any
particular historical financial loss.

## Model and timeframes

`modules.wyckoff.analyze_wyckoff` is a pure function with explicit `as_of`,
`timeframe` and direction. Model ID: `causal_wyckoff_v1`. It does not fetch
prices, send mail, place trades, read a current clock or select account risk.

The stock strategies use **1D completed exchange sessions**, including the
existing US holiday/early-close calendar. They request 180 bars; 60 valid
completed bars are the minimum, not a claim that every 60-bar window contains
a complete pattern. Chart recognition uses the selected chart timeframe and
the full supplied history, independently of the generic chart-pattern lookback.
The legacy standalone adapter explicitly maps `hour` to 4H, `1hour` to 1H,
and `day` to 1D.

The reference describes SC/BC, the subsequent automatic reaction, secondary
tests, optional Spring/UTAD, SOS/SOW and LPS/LPSY. A Spring is not mandatory.
Price-volume analysis is interpretive; it does not directly identify actual
institutional trades. The numerical thresholds below are our testable
implementation assumptions, not parameters supplied or performance-validated
by the reference. [Wyckoff Analytics tutorial](https://www.wyckoffanalytics.com/wp-content/uploads/2020/02/Wyckoff_Analytics_Wyckoff-Method_English.pdf)

## Entry and context contract

- LONG proof: SC -> AR -> ST -> SOS -> LPS plus completed recovery/hold.
- SHORT proof: BC -> AR -> ST -> SOW -> LPSY plus completed recovery/hold.
- Early phases A/B/C are chart context only, not scanner signals, tracking
  entries or signal mails. Context badges state this explicitly.
- Events carry separate observation and confirmation timestamps and separate
  chart candle coordinates; a later confirmation is not backdated as available
  at the original extreme.
- Missing, non-finite, conflicting or malformed completed OHLCV is explicit
  invalid data. Future/forming bars do not affect an earlier cutoff. Genuine
  zero volume is retained but never proves absorption or a volume-confirmed event.
- Failed ranges, failed breakouts and subsequent stop breaches invalidate the
  old sequence. Stops use ATR known at confirmation, not later volatility.
- Opposing trade-ready sequences are suppressed before the requested direction
  is selected. A high score cannot resolve contradictory evidence.
- Model scores are quality labels, never estimated win probabilities.

The first model uses historical relative-volume/spread comparisons, a
prior-decline/rally requirement, AR pivot confirmation, lower-volume ST/LPS,
and an expanded-volume SOS/SOW. See the implementation for exact inclusive
boundaries. These thresholds must be evaluated prospectively or with causal
out-of-sample replay before any tuning claim.

The standalone/chart target is explicitly a **measured-range projection**, not
a Point-and-Figure objective, confirmed support/resistance or a fill. The stock
strategy's native structural plan remains separate and is not overwritten by
these projections. Execution, native barriers, liquidity, risk/reward, costs,
freshness and mail permissions still apply independently.

## Integration and observability

Daily history is loaded once for each Wyckoff candidate, with one fixed scan
cutoff. Invalid/unconfirmed candidates are rejected before expensive native
structure and 4H enrichment. The former 220-candidate post-filter truncation
does not apply to this family. Scan diagnostics expose analyzed/confirmed
counts and bounded recognition reasons.

Result-cache reads are bound to the requested strategy/direction, including
legacy aliases and shared-cache fallback. Legacy high-score rows, malformed
proofs and unconfirmed partial rows cannot bypass this by claiming `TRADEABLE`
or `LONG_NOW`. The mail classifier and sender enforce the same evidence guard
before enrichment, deduplication and tracking, with the bounded suppression
reason `wyckoff_contract_invalid`.

The legacy OHLC-only helper is deliberately not relabelled as a repaired
Wyckoff detector: it returns unavailable evidence until its provider boundary
can supply real volume and a completed timeframe.

## Verification and remaining evidence

Offline tests cover mirrored positive LONG/SHORT sequences with and without
Spring/UTAD; missing/zero/invalid volume; future/forming candles; invalidation;
contradictory directions; price/volume scaling; input order and duplicates;
cross-adapter parity; cache identity; partial previews; mail/track suppression;
chart confirmation coordinates; and native-plan preservation.

The isolated runner blocks external sockets and SMTP and uses temporary auth,
runtime and data paths. No production users, signal exports or credentials are
used in fixtures. Final verification on 2026-09-20 after the source freeze:

- Complete root test suite: **5,252 passed, 4 skipped**, 381.43 seconds.
  The skips concern Windows symlink privileges and Linux-only file-safety
  harnesses; no Linux deployment validation is inferred from this run.
- Focused reason-registry, suppression and Wyckoff API/downstream regressions:
  **181 passed** before the final complete run.
- Frontend source/bundle verification passed: `78cc56960544`; generated
  JavaScript syntax validation passed.
- Independent core/adapter/downstream reviews completed. They additionally
  caught a range failure before automatic-reaction confirmation and a false
  Wyckoff counter for malformed non-object inputs; both have regression tests.

Browser visual QA is currently unavailable because the local computer-use
helper fails before opening the browser session. Public server health at
2026-09-20 10:11:57 UTC still reported the healthy pre-Wyckoff revision
`d9cfb3154977`. This verification did not update or restart Hetzner.

Still required after deployment: completed live scan evidence, actual provider
coverage/latency, and prospective net-of-cost results by scanner/model version.
No guaranteed profit, improved win rate, or successful Cup completion is claimed.
