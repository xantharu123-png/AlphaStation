# LEAPS implementation audit — 2026-10-14

The approved filename retains the plan date, 2026-10-13. This audit records work
and verification on 2026-10-14. It is a local implementation qualification, not
approval to activate, publish market data, place orders, or deploy.

## Outcome and safety boundary

The isolated provider → discovery → persistent runner → API → scenario path is
implemented. LEAPS remains OFF by default; missing source capabilities, endpoint
permission or display-license evidence remain unknown/denied. Synthetic proof
fixtures establish software behavior only, never actual provider rights.

Task 8 repaired reproduced integration failures: invalid stock-reference inputs,
ordinary original-time quote updates incorrectly treated as conflicts, physical
retry after pause, status lock starvation during large result reads, repeated
unchanged-contract scenario calculations, and current eligibility returning after
wall-clock rollback. Existing mail, weekly reporting, profit-factor tracking,
signal tracking and autotrader paths remain outside LEAPS.

## Meaning of the output

One standard call uses its validated original option Ask, strike, 100-share
deliverable and 100 premium multiplier. Quantity scales cash consistently; it
does not prove available fills. Expiry calculations are hypothetical, before
unknown fees, slippage and execution costs, not forecasts or profitability claims.
There is no options-price-history backtest or validated probability of profit.

Relative stock comparisons use only a positive, originally dated stock Bid that
is not future-dated or crossed. A valid dated or genuinely one-sided Bid may be
a hypothetical reference, never an executable stock-buy price. Missing/invalid
stock reference leaves relative outputs unknown while a valid option Ask can
still support explicitly dated absolute expiry algebra. No Last/Mid/FMV/chart
price or receipt-time substitution is used.

Current-comparison eligibility is separate from algebra eligibility. Original
source times, expiry, terms fingerprints, request provenance and source IDs
survive publication, reload and scenario selection. Newer original quotes replace
older observations; older replays and metadata-only repeats do not renew them.
Same-source-time contradictory quotes and actual terms conflicts fail closed.

Bid/Ask spread, quote depth, market hours, verified terms and original clock
validity matter. Unknown IV/Greeks, fees and liquidity remain unknown. Exercise
cash is distinct from premium debit and maximum premium loss. Quantity is not
fill evidence, and apparent expiry profit does not establish executable profit.

## Persistence, concurrency and controls

The LEAPS-owned v2 private envelope contains small control/authority metadata and
content-addressed snapshot references. Latest and last-complete can share one
immutable body. Native locking protects short metadata transactions and handle
capture; large body reads, actual-byte hash checks, decoding and sorting run
outside that lock. Status does not decode the full snapshot. An actual concurrent
2,000-contract result/status regression now returns 200/200, previously 200/503.

Actual bytes are SHA256-validated even for warm cached objects: same-size,
same-mtime tampering cannot silently reuse a trusted decoded object. Original
v1 private state migrates with last-complete and source clocks preserved.
Incomplete/uncommitted bodies never replace committed metadata. Former committed
blob cleanup is exact-path, regular-file-only and conservative while readers own
handles; no unrelated cache directories or old application state are cleaned.

A persistent, monotone metadata watermark denies current comparisons when the
read-decision clock regresses, including another service/process after restart.
Its initial floor includes the actual publication clock, so rollback before the
first read is also denied. Both original option and stock times are checked
against current read time; a future original source gets an explicit reason.
The trusted decision clock is sampled inside the short metadata lock and that
same value drives API assessment; differently queued monotone reads are not
mistaken for real clock regression.
It never becomes a quote date or refresh time. Invalid/missing v2 watermark
fails closed; legacy migration uses its known publication clock only as a
conservative decision floor. Control/publication cannot lower the watermark.
Capability requests do not instantiate a backend merely for this mechanism.

Pause is an acknowledgement, not immediate physical quiescence. Each newly
admitted physical attempt/retry checks its run-bound pause authority; an active
request retains its native lease until real IO/parsing finishes. Explicit resume,
new runner/run and unbound direct provider use do not inherit a stale callback.
The 900-second slice limit remains soft between safe units. The unchanged
Requests connect/read timeouts are not a hard whole-response download deadline.
Previously measured local transport evidence included a 15-byte slow body taking
14.041 s and two idle attempts taking 20.057 s; those fixtures were not rerun
merely to regenerate evidence. No watchdog prematurely releases physical IO.

## Resource qualification and remaining architecture gate

These are actual bounded synthetic measurements, not market capacity estimates.
They precede the final first-read and clock-linearization guards, which do not
change body layout, pagination, transport or the measured resource architecture.
One underlying, complete reference/chain pagination, 100/500/2,000 contracts,
six scenarios each, real provider parsing/native admission/runtime/store/ASGI;
no live history/facts/provider traffic. Traced peak means new allocations during
one call, not total RSS or retained process memory. Timings are single samples,
not a statistical latency service level.

| Contracts | Pipeline before/after (s) | Store before/after (bytes) | After page20 cold/warm (s) | After status cold/warm (s) |
|---:|---:|---:|---:|---:|
|100|0.6194 / 0.4228|2,497,611 / 1,250,729|0.09771 / 0.02407|0.00428 / 0.00330|
|500|4.3831 / 1.9782|12,428,131 / 6,215,990|0.48963 / 0.03786|0.00348 / 0.00233|
|2,000|61.8495 / 23.4964|49,716,166 / 24,860,017|1.99340 / 0.07910|0.00342 / 0.00300|

At 2,000 contracts metadata is 2,021 bytes; latest/complete share one body.
Cold page20 traced peak is 148,651,858 bytes, warm 24,935,824 bytes. Scenario
cold/warm is 1.92468/0.02346 s. The pre-review-cache-fix concurrent migrated-state
page20/status repeat was 1.77098/0.03137 s with both responses 200. These are
distinct experiments.

The private cursor still reaches 7,741,161 bytes. Full checkpoint serialization,
repeated full checkpoint processing, cold body decode and actual-byte hashing
are not bounded independently of universe size. Warm reads retain immutable
data and still verify full bytes; freshness coverage still examines original
row evidence. No universe truncation/cap was introduced. An uncapped live
full-market deployment is NOT qualified. Chunked/transactional persistence and
representative full-universe capacity qualification remain an explicit future
architecture/activation gate, not an implicit Task 8 rewrite or a full-market PASS.

## Reproducibility and QA

The pre-corner clean projection package passed 896 tests; desktop/mobile browser
qualification covered 1440×900 and 390×844, 272 synthetic API requests and 44
scenarios, with no unexpected console/page errors. Twenty explained messages
remain: ten inherited Tailwind CDN production warnings and ten injected HTTP
403/409 cases. This is not a warning-free asset build. Both unchanged source/bundle
pairs verified successfully. Browser and backend are separate fixture boundaries.

After the first-read clock reproduction, 39 focused integration tests and
899 clean-projection LEAPS/frontend tests passed without warnings or skips.
That projected C2000 concurrent result/status again returned 200/200 in
1.69030/0.04162 s. UI files/public DTO fields are unchanged by that correction;
the verified browser source/bundle pair is identical. A subsequently reproduced
out-of-order clock sampling race was corrected by sampling under the metadata
lock. Its 41 focused integration cases and 901 clean-projection LEAPS/frontend
tests passed without warnings or skips. The **PRE-review-cache-fix** full run completed in
639.29 s: **14,082 passed / 97 failed / 7 skipped / 0 errors**, exit 1. All 97
failure identities (classname and name) and all seven skip identities match the
inherited baseline; no new or missing failures. Its one warning is
`PytestAssertRewriteWarning: Module already imported so cannot be rewritten; anyio`.
This is not an overall PASS. The earlier full runs (14,077 and 14,080 passed,
each with the same 97 failures and seven skips) are respectively pre-first-read-
corner and pre-concurrency-fix, not latest-source qualification.

Independent Task 8 review then reproduced an option-clock transition defect in
cached economics: assessment changed while scenarios, break-even and active
filter results stayed stale. Fix round 1 now invalidates that cache when actual
option algebra validity changes, while preserving original evidence and unchanged-
page reuse. Partial → admission-denied pause → clock recovery/resume, the inverse
transition and the separate stock-time boundary are covered. The complete owned
integration file passed **45 tests**; the fresh clean HEAD projection passed
**905 LEAPS/frontend tests**, both without warnings or skips. The 14,082-pass
full run predates this repair and does not qualify it. Per controller Ruling 40,
a fresh immutable full suite is deferred until after the final review/fix wave.
No public DTO/UI/assets changed, so the same-byte browser evidence was not rerun.

The recorded complete offline suite qualifies its frozen pre-review working checkout, including
inherited WIP. The clean HEAD projection qualifies LEAPS and its exact two
prerequisites separately; it does not certify unrelated inherited fixes or a
clean-checkout full-project PASS.
The clean HEAD projection includes only owned LEAPS changes plus two
separately attributed, byte-identical inherited prerequisites:

| Prerequisite | SHA256 |
|---|---|
|`modules/atomic_cache.py`|`FD0B8EF062FB308CA5198881E02412FDFDCA6A7BF573102F0B03B34877360B2A`|
|`modules/ticker_detail_indicators.py`|`8B545523C95B655DBBA46DBB90D5DB96412916D03DE265F5929B68D56906A777`|

Git archive applied CRLF checkout conversion to unchanged HEAD HTML, bundle and
projected launcher files. Byte-level CRLF-to-LF comparison proves those copies
are EOL-only equivalents, not raw-byte-identical Git blobs. Projected `api.py`
matches its HEAD blob raw. The actual browser/package projection and source/bundle
verification use those recorded exported bytes; no inherited API/UI WIP is copied.

Current product/test raw SHA256 (identical in working and fix-round clean projection):

| Owned source | SHA256 |
|---|---|
|`modules/leaps/service.py`|`2FF29F708174811433A3CA711B5A54F9BEF96DD28E66E3A343949926016A44FF`|
|`modules/leaps/provider.py`|`DE71C15C58A3C2F844ACC09E2AAE8B8D69DFD197B867E73EEDAAADB3724A27DA`|
|`modules/leaps/runtime.py`|`71AA35F746D70E9069BFF273CCFFA4A806D52F72957438BB78B4D7535EA52F18`|
|`modules/leaps/api_contract.py`|`819457C384C505B74A70446EADF60F39DB509D6A49C79774ECB685D452B1E708`|
|`test_leaps_admission_isolation.py`|`F04D9E5C5744DEBB797C9DDEEDE493B1E1E9CD75A73F95F73A2A91F22C0EF28B`|
|`tests/fixtures/leaps/whole_flow.py`|`FC54952850E3CF45A34EE4070E4EB2979D6CA3007E6F647ABE89F5A481836858`|

The real helper consumers are covered: finite completed OHLCV/analytics and
finite JSON; invalid/overflow data fail closed. Atomic fsync/replace failures
preserve the prior complete result. Old helper test suites and unrelated inherited
WIP are not silently included in the clean LEAPS dependency projection.

The unchanged offline launcher uses fake credentials/private state and denies
external network/SMTP. Native Windows local sockets/locks require scoped local
execution because the sandbox stalls before sampling. No product asyncio,
broker, timeout, global-budget or launcher workaround was made. Focused runs
disable plugin autoload; the full suite restores the original plugin behavior.
The inherited full-suite reference is 13,181 passed / 97 failed / 7 skipped,
not a complete PASS. Exact failure identities, not only counts, were compared.
The user-deleted `deploy/safe_deploy.sh` remains deleted.

## Open gates

- Independent Task 8 fix re-review, final whole-branch review and post-review full suite.
- Uncapped full-market capacity and bounded transport/response requirements.
- Separately authorized, at most three-request live capability check; verified
  source/session/terms evidence and actual multiuser display-license evidence.
- Explicit activation/composition authorization; feature remains OFF otherwise.
- Remote push, deployment, server revision and production acceptance remain
  separate from scoped local Git publication recorded in the implementation
  handoff. No push/deploy/live activation was performed; an unpushed local commit
  cannot be installed by a normal server pull.

Inherited API/UI WIP, unrelated source/tests/docs and the deleted deploy script
are preserved. The controller owns the central TODO/progress record. No accounts,
subscriptions, licenses, secrets, orders, SMTP or server changes were made.
