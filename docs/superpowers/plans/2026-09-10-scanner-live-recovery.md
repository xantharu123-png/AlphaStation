# Scanner Live Recovery Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development task-by-task with independent review. Execution is authorized by the user's request to repair and verify the scanners; production completion still requires fresh server evidence.

**Goal:** Restore diagnosable complete scanner runs and verify the signal/mail chain without weakening trading contracts.

**Architecture:** Separate provider transport and field availability from strategy selection. Add bounded safe diagnostics before selecting a price-source repair; transient GET recovery must never publish partial scans as successful. Preserve existing execution and mail guards.

**Tech Stack:** Python, requests, FastAPI, stdlib-only SSH diagnostics, Windows PowerShell, pytest.

**Spec:** `docs/SCANNER_RELIABILITY_REPAIR_2026-09-09.md`; current private evidence reviewed separately in `output/profitability/AUDIT_HETZNER_20260910T083522Z.md` (never stage private evidence).

## Execution status — 2026-09-10

- Task 1 locally complete (`cbb165b`), independently approved.
- Task 2 locally complete (`7b462f6`), independently approved; live execution
  requires the user's SSH authentication. The reviewed command was supplied.
- Task 3: BI factor compatibility and existing app/mail routes reviewed; no
  factor change justified. Price-source decision remains blocked on the live
  provider field evidence. No fallback, mail subscription or entitlement change.
- Task 4: source regression 4,595 passed / 4 skipped; 54 new probe tests passed
  separately. Whole-source review clean. Publication and live operational
  acceptance are separate; this is not an all-scanners-working claim.
- Final integrated rerun on `main` at `2f757e6`: **4,649 passed / 4 skipped**,
  298.94 seconds, including the new probe tests. No source edits afterwards.

The checklists below describe the acceptance contract; local completion does
not mark unobserved production steps complete.

## Global Constraints

- BI stays at least 17/20; below 17 no signal, watchlist, tracking or mail.
- Keep final-cache preservation and fail-closed incomplete coverage.
- No fabricated prices, day-close-as-live fallback, lowered liquidity/freshness/RR requirements, new orders or wider risk limits.
- No new mail subscriptions, no historical tracker mutation, no secrets/raw provider bodies/URLs in output.
- Remote diagnostics use the existing service configuration, never import repository code as root, and never disable TLS or host verification.
- Git publication and deployment are separate; operational acceptance needs current server revision, complete scans and correct mail outcomes. No guaranteed signal count or profit.
- Work on `codex/scanner-live-recovery` in the existing user workspace. Preserve untracked private output. No destructive recovery or recursive ownership changes.

### Task 1: Bounded BI transport recovery and safe failure evidence

**Files:** Modify `modules/scanners.py`; create `modules/bi_transport.py` if a focused transport helper avoids duplicated request logic; create `test_bi_transport_recovery.py`; extend `scripts/collect_server_evidence.py` and `test_collect_server_evidence.py` for fixed diagnostic fields only. Update the old single-attempt assertion in `test_bi_run_integrity.py` only where transient Timeout/ConnectionError now retries; preserve all failed-run/cache/mail assertions.

**Interface:** Existing `ScannerDataError.code` values stay compatible. Additional fixed diagnostic reason/counter fields distinguish timeout, connection failure, TLS failure, HTTP unauthorized/rate-limited/server/client error, malformed JSON and unexpected failure. No exception text, ticker or URL in exported transport metadata.

- [ ] Reproduce the current abort using an actual `_bi_background_scan` test with a fake network boundary: first daily-history request raises `requests.exceptions.Timeout`, second returns the existing valid completed-bar fixture. Before implementation it aborts; after it completes a legitimate under-17 zero run without replacing old results on exhausted errors.
- [ ] Add deterministic behavioral tests for transient 503 then success; three timeouts; 401/403/429 no retry; TLS no retry; malformed JSON no retry; stop request during recovery; redaction; a global retry budget; collector allowlisting and old payload compatibility.
- [ ] Use at most three attempts per request, retry only Timeout/ConnectionError excluding TLS and fixed HTTP 408/500/502/503/504. At most 20 extra requests across one BI run, with 0.5 then 1 second backoff. Use the existing rate-limited getter for every retry. Stop requests end without publishing a successful final cache. Recovered failures are counted as recovered transport incidents, not unresolved data failures. Exhaustion remains fatal with the existing public code and a safe reason.
- [ ] Limit changes to required BI universe/history requests; optional movers remain optional. Do not change unrelated scanners' fetch policy. Compare red/green tests and existing BI outcome/contract/collector suites with isolated data directories.
- [ ] Obtain independent task review; commit reviewed task files only.

### Task 2: Standalone live price-field diagnostic

**Files:** Create `scripts/probe_stock_provider.py`, `scripts/probe_hetzner_provider.ps1`, `test_stock_provider_probe.py`.

**Interface:** CLI through `/usr/bin/python3 -I -`, one bounded aggregate JSON object with schema version, UTC capture time, safe source/config confidence, fixed endpoint statuses and field-state counts. No tickers, prices, keys, key hashes, account or recipient values in output.

- [ ] Read actual API configuration precedence and service identity. Use the service HOME, not SSH root HOME. If startup-vs-current configuration equality cannot be proved, label it unknown; do not claim an exact effective in-memory key.
- [ ] Write failing tests for missing/null/zero/negative/bool/nonfinite/string field values, bounded reads, safe error handling, config precedence, service identity, no symlink/special-file credential reads, network destination/redirect restrictions and no leaked exception/provider text.
- [ ] Implement a stdlib-only standalone diagnostic, using fixed HTTPS provider paths and no environment proxies or redirects. Bound request count, timeout, response bytes and rows. Initially use only two GETs: full snapshot and fixed liquid control snapshot. A movers-merge probe is only needed if full-snapshot Trade fields exist but scanner input lacks them. Do not alter service files, caches, DBs or start scans.
- [ ] The PowerShell wrapper prompts for SSH password in the user's terminal, validates the output shape and creates a new private output file without overwrite; scripts remain ASCII for Windows PowerShell stdin.
- [ ] Obtain independent review and tests. Execute remotely only with authenticated access; never request a password in chat. If access still needs user action, provide one precise local command.

### Task 3: Evidence-dependent strategy and mail repair

**Files:** Determined only by a reproduced defect in the existing price extraction, snapshot merge, configuration, or mail route. No speculative implementation is authorized by this plan.

- [ ] Analyze Task 2 evidence against the code path and official provider field contract. Distinguish absent entitlement, market-session reset, malformed values and merge corruption.
- [ ] For each confirmed implementation defect, first capture a failing behavioral regression, then make the smallest repair without relaxing source/timestamp or execution requirements. If the account lacks a required entitlement, report that external dependency instead of fabricating it or buying a plan.
- [ ] Review BI range/Fibonacci window compatibility using real analysis functions and independently derived closed-bar cases. Keep compatible but selective rules unchanged; instrument missing proof rather than optimizing to force hits.
- [ ] Verify scanner-specific App-only/Info/Trade routes. Do not infer a transport failure from no generated signal, and do not automatically attach new email broadcasts to display-only lists.

### Task 4: Release and operational acceptance

- [ ] Run the integrated regression suite with isolated data/runtime paths; check syntax, private-file exclusion and diff whitespace. Review the whole patch independently.
- [ ] Commit and push only reviewed in-scope changes to the verified upstream; retain the distinction between branch push, main integration and deployment.
- [ ] Deploy only when authenticated server access and clean fast-forward conditions are verified; stop on local server changes or ownership conflicts instead of overwriting them. Verify services, revision and health after restart.
- [ ] Require complete current Long and Short runs and complete stock-strategy attempts during a data-supported session. A technically valid zero scan remains possible and is not a profitability claim.
- [ ] Verify actual existing mail route outcomes and transport acceptance where signals qualify, without bypassing gates or sending artificial trade signals.
- [ ] Report completion only when those operational checks are observed; otherwise name the specific remaining dependency. Quiet follow-up may watch for newly supplied evidence, but cannot invent server authentication.
