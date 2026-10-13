# LEAPS Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Einen eigenständigen US-Aktien-/ADR-Scanner für belegte langfristige
gekaufte Calls mit korrektem Kontraktvergleich und Verfallsszenarien bauen.

**Architecture:** Ein isoliertes Python-Paket trennt Kontrakte, Datenadapter,
Basiswert-Evidenz, Rechnung und Laufzeit. API und vorhandene React-Oberfläche
integrieren nur diese Schnittstellen; der Aktien-Tracker erhält keine Optionen.
Provider, Clock, Budget, Speicherung und Unternehmensanalyse sind injizierbar.

**Tech Stack:** Bestehendes Python/FastAPI/requests/pytest, stdlib Decimal und
dataclasses, vorhandenes React in `frontend/index.html`. Keine neue Abhängigkeit.

**Spec:** `docs/superpowers/specs/2026-10-13-leaps-scanner-design.md`.
Architektur freigegeben am 13.10.2026; **Planprüfung/Ausführungsmethode offen**.
Dieses Dokument enthält geplante Schnittstellen/Tests, keinen implementierten Scanner.

## Global Constraints

- Eigener Scanner **LEAPS** unter Aktien; kein WATCH-/Beobachtungsbranding und
  keine neue „JETZT kaufen“-Behauptung.
- **12 bis 24 Kalendermonate**, inklusive Grenzen; Monatstage auf Monatsende
  klemmen. Kein 365–730-Tage-Ersatz, keine Acht-Ticker-/Momentum-/BI-Zulassung.
- Standardkontrakte mit **100 Aktien und Premium-Multiplikator 100**; Call,
  USD, belegte Standard-Equity-/ADR-Bedingungen. Unbekannte/angepasste ausschließen.
- Decimal für Geld/Strikes/Grenzvergleiche; gerundet wird nur die Anzeige.
- Quotes höchstens **30s Zeitversatz**, Echtzeit maximal **60s** alt, belegter
  verzögerter Feed maximal **Feedlag plus 60s**. Empfang erneuert keine Quelle.
- Höchstens **ein gleichzeitiger Providerrequest**; pro Minute höchstens
  `min(20, floor(0.10 × nachgewiesenes gemeinsames Budget))`.
- Während aktiver vorrangiger Aktien-/BI-/Alert-Datenläufe keine neuen LEAPS-
  Requests; keine Änderung alter Scannerbudgets, Retries oder Timeouts.
- Ein geteilter Run-Owner **leaps**; Start/Pause/Fortsetzen **nur Admin**,
  Ergebniszugang separat freigegeben. Feature deaktiviert bis Daten-/Rechteprüfung.
- Versionierte atomare Datenhaltung unter **ALPHA_DATA_DIR/leaps**;
  letzte vollständige Ergebnisse erhalten, Teilstände getrennt.
- **Zuerst Basiswertchart**, dann Kontraktvergleich. Ein Fortschrittsbalken,
  Details geschlossen, gespeicherte Ergebnisse beim Öffnen laden.
- Keine Orders, Abos, neuen Mailkanäle/-Einstellungen, Tracker-/Reminder-
  Migration, Landingpageänderung oder Umstellung der Serverinstallation.

## Review Focus

1. Alter unveränderter NBBO bei frischem Snapshot: Quelle nicht durch Empfang
   erneuern; nur datierte Rechnung zeigen. Test in Task 3.
2. Referenz vollständig, einzelne Chain aber unvollständig: keine globale
   „keine Treffer“-Behauptung und keine falsche Abdeckungsquote. Test in Task 4.
3. Pause/Resume über einen Datums-/Versionswechsel: keine alten Cursor/Quotes
   in einen neuen Vergleich mischen. Test in Task 5.
4. Schneller Wechsel von Strike/Verfall oder Tab bei verspäteter Antwort:
   keine Kontrakt-/Preisverwechslung. Test in Task 7.
5. Fehlende oder unbelegte Greek-/IV-Einheiten und Sonderdeliverables: kein
   erfundenes 0/35%-Delta/Standard-100. Tests in Tasks 1 und 3.

## Verifizierte API-Basis und offene Zugangsgrenzen

Aktiver Code liest `POLYGON_KEY` in `api.py`; bestehende Abrufe verwenden
`api.polygon.io`. **Kein aktiver LEAPS-Quote-/Greek-Pfad vorhanden.** Archivierte
Unusual-Options-Logik untersucht wenige nahe Strikes/Volumen und ist kein Ersatz.
Massive unterstützt die benötigten Ressourcen technisch; tatsächliche
Account-Rechte sind weiterhin **UNVERIFIED**, nicht als fehlendes Abo bewiesen.

| Bedarf | Adapterressource | Verbindlicher Unterschied |
| --- | --- | --- |
| Kontrakte | `/v3/reference/options/contracts` | `call`, Laufzeitbereich, `as_of`, `expired=false`; maximal 1000/Seite, alle `next_url` |
| Marktbeobachtung | `/v3/snapshot/options/{underlyingAsset}` | maximal 250/Seite; Quote-/Greek-Felder können fehlen |
| US-Basiswerte | `/v3/reference/tickers` | `market=stocks`, erlaubter Typ, Datum/active; US-Locale **in Antwort prüfen**, keinen undokumentierten Filter erfinden |
| Aktienvergleich | `/v2/last/nbbo/{stocksTicker}` oder belegter gleichwertiger Snapshot | eigener Ask/Originalzeit; `underlying_asset.price` ist kein Ask |

Referenzzugriff beweist weder Quotezugriff noch unverzögerte Daten. Stock- und
Optionsrechte sind getrennt. OI bezieht sich auf den letzten Handelstag. Die
Snapshot-Ressource ist keine historische Optionsdatenbasis. Eine technische
Probe bestätigt keine Mehrnutzer-/Displaylizenz.

Standardklassenevidenz stammt aus OCC-Spezifikation: Standard-Equity-/ADR-
Option, 100 Aktien, Premium-Punkt = 100 USD, amerikanisch und Aktienlieferung.
Das ist **normative Produktevidenz**, kein erfundenes Providerfeld. Pro Kontrakt
zusätzlich Listing, Recht, Strike/Verfall/Symbol, 100 Aktien, Corporate-Action-
und Deliverable-Evidenz prüfen. `premium_multiplier = shares_per_contract`
ist ausdrücklich keine allgemeine Regel. Optional fehlendes
`additional_underlyings` gemäß Providerschema behandeln; bei anderer
unbekannter wesentlicher Bedingung `conditions_unknown`.

IV-Wire-Skala/Annualisierung und eigener Greek-Modellzeitpunkt sind in den
gelesenen Snapshot-Docs nicht eindeutig belegt. Zunächst raw samt Provenienz
speichern, normalisierte IV ohne Nachweis leer lassen. Nicht 5/35 heuristisch
durch 100 teilen. Aus quote.last_updated keinen Greek-Modellzeitpunkt erfinden.

Options-Handelssitzungen pro Klasse/Exchange belegen: keine pauschale RTH-
Schranke. 2026 wurden Erweiterungen für ausgewählte Klassen angekündigt; deren
damals erwartete Liste ist keine aktuelle Eligibility-Autorität. Unbekannte
Sitzung erlaubt datierte Werte, aber keine aktuelle Ausführungsbehauptung.

## Dateigrenzen und gemeinsame Interfaces

Neu: `modules/leaps/` mit leeren, nebenwirkungsfreien `__init__.py`,
`models.py`, `contracts.py`, `pricing.py`, `provider.py`, `underlying.py`,
`admission.py`, `runtime.py`, `service.py`. Jede Datei hat die Aufgabe der
nachfolgenden Task, keine globale API-/BG-Initialisierung.

Testdateien jeweils `test_leaps_<task>.py`; Fake-Providerfixtures nur unter
`tests/fixtures/leaps/`, klar synthetisch und ohne private Daten/Schlüssel.
Integrationen: kleine gezielte Änderungen in `api.py`, `frontend/index.html`,
`frontend/app.bundle.js`; neue Berechtigungen nicht default-allow setzen.

Alle Task-Interfaces verwenden diese in Task 1 definierten Typen:

```python
OptionContract(id, provider_symbol, underlying, right, strike, expiry,
               currency, shares, premium_multiplier, exercise_style,
               settlement, terms_fingerprint, evidence)  # frozen; Decimal/date
QuoteObservation(bid, ask, bid_size, ask_size, source_ns, received_ns,
                 size_unit, feed_mode, feed_lag_s, source) # nullable metadata
UnderlyingObservation(ticker, price, price_kind, source_ns, feed_mode)
QuoteAssessment(algebra_eligible, comparison_eligible, liquidity_status,
                reasons, expiry_ns)                     # distinct decisions
GreeksObservation(delta, iv, raw, units_evidence, model_evidence, source_ns)
CapabilityEvidence(reference, option_quotes, stock_quotes, standard_terms,
                   sessions, budget, display_rights, captured_ns, evidence_ids)
PageResult(items, next_cursor, complete, reason, request_id)
UnderlyingEvidence(ticker, indicators, facts, coverage, sources, as_of)
ExpiryResult(debit, intrinsic, pnl, premium_roi, break_even,
             exercise_cash, stock_pnl, stock_price_roi, delta_shares)
AdmissionDecision(allowed, reason, retry_after_s)
RunSpec(run_id, market_date, filters, universe_revision, schema_version,
        methodology_version)
LeapsSnapshot(run, status, data, coverage, reasons, cursor, sources)
```

Immutable typed objects; monetary JSON fields serialize as Decimal **strings**,
ns epochs as integer strings, ratios as fractions (`0.35`, not `35`). Browser
does presentation rounding only; expiry calculations run on server. Existing
`parse_strict_number` returns float and is not the monetary parser for LEAPS.
Provider JSON parses decimal tokens directly, before binary-float conversion.

## Task 1: Contract identity, terms and bounded numeric inputs

**Files:** Create `modules/leaps/__init__.py`, `models.py`, `contracts.py`;
test `test_leaps_contracts.py`.

**Interfaces:** Produces the shared types above, `parse_decimal(value) -> Decimal`,
`parse_standard_contract(raw: Mapping, listing: Mapping, evidence: Mapping)
-> OptionContract`, `expiry_window(as_of: date) -> tuple[date, date]`.
Invalid essential conditions raise `LeapsDataError(code, scope)`; scope is
`contract`, `underlying` or `provider`, never an unredacted transport exception.

- [ ] **Step 1:** Tests pin SOFI symbol `O:SOFI280121C00015000`, strike 15,
  expiry 2028-01-21, call, separate identity for strike 16; metadata mismatch,
  extra cash/150 shares/unknown standard proof rejected. Provider root aliases
  must not be compared blindly to dotted share-class tickers.
- [ ] **Step 2:** Run `.\.codex_pytest_env\Scripts\python.exe -B -m pytest -q -p no:cacheprovider test_leaps_contracts.py`; expect RED missing module/interfaces.
- [ ] **Step 3:** Implement exact interfaces. Numeric lexical input at most
  64 characters, at most 12 fractional places, finite magnitude <= 10^12,
  no bool/float/nonfinite/locale wrappers; strike/ask > 0. Quantity integer
  1..10^6, not bool/string/fraction. Decimal context precision 50 and
  ROUND_HALF_EVEN in local context, no change to
  process-global context. The latter input bounds are resource controls, not
  trading filters. UTC-aware time and ns-unit checks, calendar-month clamping.
- [ ] **Step 4:** Run same file GREEN, including Jan31/leap-day boundaries,
  all optional/essential-field combinations and normalized Greek-unit absence.
- [ ] **Step 5:** Review/commit only this new package subset and test file.

## Task 2: Pure expiry arithmetic and independent mathematical oracle

**Files:** Create `modules/leaps/pricing.py`; test `test_leaps_pricing.py`.
**Interfaces:** Consumes Task 1. Produces
`evaluate_expiry(contract, ask: Decimal, terminal: Decimal, *, quantity: int = 1,
underlying: UnderlyingObservation | None = None, delta: Decimal | None = None)
-> ExpiryResult`, `spread_metrics(bid: Decimal | None, ask: Decimal)
-> tuple[Decimal | None, Decimal | None]`,
`scenario_prices(reference: Decimal | None) -> tuple[Decimal, ...]`.

- [ ] **Step 1:** Write parameterized SOFI assertions: terminal 0/15/15.34/
  19.30/22/26/30 → P&L −430/−430/−396/0/270/670/1070. Assert debit 430,
  BE19.30, exercise cash1500. At +25% (`19.175`) assert call −12.50,
  stock +383.50, rather than call +25%. Three contracts scale cash ×3.
- [ ] **Step 2:** Run `python -B -m pytest -q -p no:cacheprovider test_leaps_pricing.py`
  using the same existing interpreter; expect RED missing functions.
- [ ] **Step 3:** Implement the spec algebra. `S_T=0` valid, missing/nonpositive
  `S_0` leaves relative values null; stock result is **price return before
  dividends, costs and financing**, not total return. Unknown fees stay unknown.
  No pre-expiry pricing model; no hypothetical dividend-adjusted net ROI.
- [ ] **Step 4:** GREEN with an independent `fractions.Fraction` oracle for
  cash results: convex/nondecreasing payoff, exact BE, quantity scaling,
  `call_pnl-stock_pnl = 100n(S_0-K-A)` above strike. Pin 19.299999/19.30/
  19.300001 → −0.0001/0/+0.0001 and `K=.10,A=.20,S_T=.30` → exactly0.
  Bid4/Ask4.30 → spread `6/83`, ask-loss `3/43` within Decimal precision;
  no bid gives unknown spreads, crossed quote invalid, equal bid/ask valid.
- [ ] **Step 5:** Independent reviewer checks tests against spec/OCC, then
  commit only pricing and its tests. Numerical PASS is not strategy profitability.

## Task 3: Strict provider adapter, quote assessment and capability probe

**Files:** Create `modules/leaps/provider.py`,
`scripts/probe_leaps_provider.py`; test `test_leaps_provider.py`.
**Interfaces:** `MassiveLeapsProvider(transport, clock_ns, admission, capability)`;
`list_contract_page(run, cursor=None) -> PageResult`,
`list_listing_page(kind, run, cursor=None) -> PageResult`,
`chain_page(underlying, run, cursor=None) -> PageResult`,
`underlying_quote(ticker) -> QuoteObservation`,
`daily_history(ticker, *, completed_through, as_of) -> Mapping`,
`company_facts(cik, *, configured_sec_user_agent) -> Mapping`,
`assess_quote(option, underlying, *, now_ns, verified_session)
-> QuoteAssessment`, `probe_capabilities(provider, *, max_requests=3) -> CapabilityEvidence`.

- [ ] **Step 1:** Tests use fake responses with authentic schema and coherent
  symbols, not the inconsistent documentation sample. Pin 401/403 globally
  denied, missing `last_quote` not entitlement success, OI previous-session,
  options nanoseconds vs stock aggregate milliseconds, missing Greeks/IV units.
- [ ] **Step 2:** Run `test_leaps_provider.py` with the offline pytest command;
  expect RED, no authenticated calls or secrets loaded.
- [ ] **Step 3:** Implement injected transport, Decimal JSON parsing, trusted
  HTTPS origin/path/cursor validation and cycle detection. Timeout `(3,10)`s,
  at most one retry for connection/5xx; 401/403 no retry, 429 pause, redirects
  rejected. Headers carry private credentials, never saved/returned URLs.
  Bid/ask sizes count contracts for options, shares for stock quotes; missing
  or zero size is insufficient fillability evidence, not a fabricated fill.
  Daily stock requests and SEC requests pass the same LEAPS priority/request-
  count gate. SEC uses its own public HTTPS host and configured user agent,
  never the market API key. Only market calls consume the shared market token.
  Do not call legacy fetch helpers that perform hidden unadmitted retries.
- [ ] **Step 4:** GREEN: original quote at `now−60s` accepted, one ns older
  not current; known15m lag allowed up to960s, unknown lag never realtime;
  `source_ns>now` invalid. Reference/Greek clock cannot borrow quote clock.
  Original old unchanged NBBO with new receipt retains dated algebra only.
  Bid0/missing can retain positive-Ask isolated algebra, no adequate liquidity;
  FMV/Last/Mid not Ask. Session proof uses applicable class, not universal RTH.
  Bei synchronen beidseitigen Quotes `Ask_call + K < Bid_stock` als auffällige
  Preisrelation kennzeichnen, nicht als garantierte Arbitrage/Topchance;
  isolierte hypothetische Algebra bleibt möglich. Ein letzter Aktienkurs
  genügt für diese aktuelle Preisrelationskontrolle nicht.
- [ ] **Step 5:** Commit adapter/test/probe. Probe defaults `--dry-run`, requires
  explicit `--execute --max-requests 3 --underlying SOFI`, prints only capability
  summaries, no key/email/raw response. Do not run `--execute` in offline QA.

## Task 4: Complete universe, underlying evidence and contract comparisons

**Files:** Create `modules/leaps/underlying.py`, `service.py`;
tests `test_leaps_universe.py`, `test_leaps_service.py`.
**Interfaces:** Consumes Tasks1–3. Produces
`build_underlying_evidence(ticker, *, completed_bars, facts, sources, as_of)
-> UnderlyingEvidence`, `discover(provider, run, checkpoint) -> LeapsSnapshot`,
`compare_contract(contract, observation, evidence, run, now_ns) -> Mapping`.
The provider injects a batched/cache-backed daily-history/facts loader; no API import.

- [ ] **Step 1:** Tests include >8 tickers, >20 pages, `CS/ADRC/ADRP` listing
  variants, US/foreign/ETF/unknown classes, real empty window, duplicate/conflict,
  cycle, failed second Chain page. Reference candidates missing from completed
  Chain remain unobserved/excluded with evidence, not secretly evaluated.
- [ ] **Step 2:** Run these two tests RED with fake transport/history/facts.
- [ ] **Step 3:** Implement full reference/stock intersection and all applicable
  Chains; types from existing supported US-equity scope validated, not names.
  Use pure `build_ticker_detail_indicators(completed_bars)` and
  `analyze_company_facts(companyfacts, *, price, market_cap, shares_outstanding, industry)`
  behind the admitted provider/cache in a worker, not the request eventloop.
  Direct `fetch_business_quality` would bypass per-request LEAPS admission and
  is not the new live-fetch path. SEC CIK comes from verified listing reference;
  unavailable CIK/user agent/facts leaves quality partial, never fake zero.
  Require actually completed session for history, validate entire causal OHLCV;
  legacy strict-history parser may validate only its unpaginated contract.
  Facts stay live/non-point-in-time; banks/REIT partial.
- [ ] **Step 4:** GREEN: truthful distinct coverage counts, denominator unknown
  until reference ends, contract-local issue retains others, provider failure
  visible, no swing gate. Default rank raw BE-rise with stable contract ID;
  null at end. User filters preserve missing-evidence category and fingerprint.
  Batch/cache fixtures prove SEC calls not performed once per option contract.
- [ ] **Step 5:** Commit new files/tests only; inherited helper dependencies
  must be separately settled before publication, never silently included.

## Task 5: Bounded admission, persistent run and LEAPS-only controls

**Files:** Create `modules/leaps/admission.py`, `runtime.py`;
test `test_leaps_runtime.py`.
**Interfaces:** `Admission(shared_budget, priority_active, clock_ns).try_acquire(provider_scope)
-> AdmissionDecision`, `LeapsRunner(provider, store, admission, clock_ns)` with
`start(run: RunSpec) -> LeapsSnapshot`, `pause(run_id) -> LeapsSnapshot`,
`resume(run_id) -> LeapsSnapshot`, `snapshot() -> LeapsSnapshot`;
`LeapsStore(root, atomic_writer)` with `publish(snapshot) -> None`,
`load() -> LeapsSnapshot | None`. Start/control returns acknowledgments without
blocking on the full scan; worker owns `run_slice() -> LeapsSnapshot`.
Atomic writer supplied as `write_json_atomic`; no standalone import of API.

- [ ] **Step 1:** Tests prove one request/worker, cap20 for budget200, cap5 for
  budget50, unknown/invalid/zero allocation → no live I/O. Higher-priority
  active loop means wait. Global budget failures cannot borrow old fail-open.
  Provider scope is `market` or `sec`: both count toward LEAPS request cap,
  only `market` consumes the shared Polygon/Massive budget token.
- [ ] **Step 2:** Run `test_leaps_runtime.py` RED with deterministic fake clock.
- [ ] **Step 3:** Implement own run/control registry and serialized admission
  under the applicable shared-budget lock; no double token consumption or
  in-process-only substitute. No changes to old stock control/policy rules.
  Persist resumable stage/cursor and contract-ID dedupe; 15-minute work slice
  yields a partial/resumable snapshot, never complete-zero. Request timeout
  bounds pause latency, no provider kill and no browser-abort→backend-stop.
- [ ] **Step 4:** GREEN for failed atomic write retaining previous final,
  fresh read without run, same-run resume vs new date/version/filter restart,
  stale cursors, concurrent start → existing run, paused counts and one bar.
  Cache writes use private namespace; test no writes to tracker/reminder paths.
- [ ] **Step 5:** Commit runtime/admission/tests only. No scheduler registration
  for automatic scans; restart resumes only by an explicit valid admin action.

## Task 6: Thin authenticated API, feature and data/license gates

**Files:** Modify targeted LEAPS additions in `api.py`; create
`modules/leaps/api_contract.py`; test `test_leaps_api.py`.
**Interfaces:** `LeapsAccessPolicy` supplies feature flag, admin status, endpoint
capabilities and separately documented display rights. Endpoints:
GET `/api/leaps/capabilities`, `/api/leaps/results`, `/api/leaps/status`;
POST `/api/leaps/scan`, `/api/leaps/control`, `/api/leaps/scenarios`.
Scenario input: contract ID, snapshot/run ID, quantity integer, terminal prices
as decimal strings. Response monetary fields never browser floats.

- [ ] **Step 1:** API tests pin authenticated access, customer403 for run control,
  default feature disabled, missing rights not zero results, 409 stale run ID,
  quantity/schema422, CSRF/origin rules matching existing cookie framework.
- [ ] **Step 2:** Run via `.\.codex_pytest_env\Scripts\python.exe -B scripts\run_offline_tests.py -q --tb=short test_leaps_api.py`; expect RED route absence.
- [ ] **Step 3:** Inject the new service in thin worker-backed routes. No global
  `/api/scan-control` reopening, customer-tab default allow or paid-plan change.
  One manual throttle path; data requests only for enabled, proven capability.
  Namespace/permission denial precedes any cache data disclosure or worker start.
- [ ] **Step 4:** GREEN offline against fake provider with eventloop responsiveness,
  initial saved-result load, malformed cache/error envelope and no SMTP/tracker/
  broker calls. Scenarios require matching server-owned stored contract/Ask;
  client cannot supply fabricated terms or quote to obtain a trusted result.
- [ ] **Step 5:** Review the isolated API delta against inherited baseline before
  scoped commit; no whole-file staging of unrelated audit changes.

## Task 7: Compact chart-first React flow and browser regression

**Files:** Modify scoped components/menu in `frontend/index.html`; regenerate
`frontend/app.bundle.js`; create `test_frontend_leaps_contract.py` and
`tests/browser/leaps_flow.js` (provider-free fixture routes).
**Interfaces:** `LeapsTab` consumes normalized results/status/scenario API;
`LeapsContractDetail` holds contract+run identity, opens explicitly labelled
underlying chart through existing ticker selection. Reuse `useScannerFeed`
for cache/poll scope where its contract applies, not stock-entry rendering.

- [ ] **Step 1:** Frontend source/behavior tests assert LEAPS-only navigation,
  chart first, distinct stock/premium, no WATCH/NOW, exactly one progress bar,
  stored initial data, nullable IV/Delta/fees and collapsed details.
- [ ] **Step 2:** Run frontend test RED through the offline launcher.
- [ ] **Step 3:** Implement paginated compact table and typed contract detail,
  default raw-BE-rise sort, date/feed label, separate limitations. Buttons obey
  admin/capability state with visible disabled/waiting reason. No new customer
  billing access and no modification of landingpage or existing preferences.
- [ ] **Step 4:** GREEN offline then browser at 1440×900 and390×844: slow response
  after strike/tab/run change ignored; initial cache displays; expired current
  quote becomes dated and never regains freshness from render. No JS error.
  Run `node scripts/build_frontend_bundle.js` and
  `.\.codex_pytest_env\Scripts\python.exe -B scripts\verify_frontend_bundle.py`.
  Browser fixture runner accepts `--base-url` and a fresh private `--output`,
  exits nonzero on assertions/pageerrors, and uses bundled Playwright resolved
  through workspace-dependency metadata, not a new product dependency.
- [ ] **Step 5:** Review bundle/source ownership and save fresh screenshots in
  new private output folder. Scoped commit after inherited frontend changes
  are reconciled; do not publish a bundle missing its source/dependencies.

## Task 8: Whole-flow audit, real capability gate and honest handoff

**Files:** Create `test_leaps_admission_isolation.py`;
document `docs/LEAPS_IMPLEMENTATION_AUDIT_2026-10-13.md`; update `TODO.md`.
**Interfaces:** Uses every preceding task, no new price/strategy model.

- [ ] **Step 1:** Write failing end-to-end fixture tests for identical Ask/terms
  and source IDs from provider → service → API → scenario response; no stock-
  quote/option-premium/quantity/unit mixing. Pins no mail/weekly/PF/autotrader
  record for any LEAPS output, including duplicate/adjusted/failed rows.
- [ ] **Step 2:** Reproduce RED before any isolation repair, fix narrowly, run
  all `test_leaps_*.py` and frontend file GREEN through offline launcher.
- [ ] **Step 3:** Fresh independent reviewers check deterministic math against
  Fraction oracle, market evidence/units, and practical Ask/Bid/size/spread/
  exercise/liquidity limitations. Remove profitability claims unsupported by
  options price history. Source freeze before the full offline regression.
- [ ] **Step 4:** Run unchanged complete offline suite and compare exact failure
  identities to inherited baseline, not just counts. Earlier baseline was
  13,181 passed/97 failed/7 skipped; not a fresh result or complete PASS.
  Live capability probe remains a separately authorized max3-request check;
  missing quote rights/IV scale/session/terms stays unknown, not invented.
  Actual multiuser display access requires license evidence separately.
- [ ] **Step 5:** Update TODO with implementation/QA/access/publish/deploy states,
  actual source hashes and remaining gates. Offer scoped Git publication only
  after integrated source/dependencies are reproducible; server activation is
  separate. No normal-pull instruction pretending unpushed code is installed.

## Inherited work and execution boundary

Start from the current audited checkout, not remote HEAD alone: source repairs,
`modules/atomic_cache.py` and other helpers are currently uncommitted/untracked.
Preserve all WIP/private output and deleted deploy script. Before execution
record source/index hashes; task commits stage only owned files/deltas. A new
worktree starting at HEAD would omit these dependencies; it is not an equivalent
baseline. Isolation must carry the approved source state or remain in the
current checkout with a recorded baseline. Never blanket-stage to solve this.

No product implementation has started. Next: user reviews this plan and chooses
**Subagent-driven** (recommended for separate mathematical/data gates) or
**Native** (one implementer, independent whole-flow review at the end).

## Primary references for implementation review

[Massive Chain Snapshot](https://massive.com/docs/rest/options/snapshots/option-chain-snapshot),
[Contracts](https://massive.com/docs/rest/options/contracts/all-contracts),
[Stocks reference](https://massive.com/docs/rest/stocks/tickers/all-tickers),
[stock NBBO](https://massive.com/docs/rest/stocks/trades-quotes/last-quote),
[Greek methodology](https://massive.com/blog/greeks-and-implied-volatility),
[2026 extended sessions](https://massive.com/blog/extended-hours-are-coming-to-opra-options-data-2),
[OCC standard equity terms](https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications).
