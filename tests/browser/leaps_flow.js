"use strict";
// Provider-free fixture boundary; shipped app, real React and real candles.
const fs = require('fs'), path = require('path'), http = require('http'), assert = require('assert/strict');
const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, arg, i, list) => arg.startsWith('--') ? [...pairs, [arg.slice(2), list[i + 1]]] : pairs, []));
const base = new URL(args['base-url'] || 'http://127.0.0.1:8087');
if (!['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname)) throw new Error('Fixture browser requires local base-url');
if (!args.output || fs.existsSync(args.output)) throw new Error('--output must name a fresh private directory');
fs.mkdirSync(args.output, { recursive: true });
const playwrightPath = args['playwright-module'] || process.env.ALPHA_PLAYWRIGHT_MODULE;
if (!playwrightPath) throw new Error('Resolve ALPHA_PLAYWRIGHT_MODULE with workspace-dependency metadata');
const { chromium } = require(playwrightPath);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const deadline = Date.now() + 60 * 1000;
const ns = ms => String(BigInt(ms) * 1000000n);
const policy = (admin = true) => ({ enabled: true, admin, results_allowed: true, display_rights_verified: true,
  endpoint_capabilities: { results: true, status: true, scenarios: true, scan: true, control: true },
  capabilities: { reference: true, option_quotes: true, stock_quotes: true, standard_terms: true, sessions: true, budget: true } });
const expiryResult = (quantity = 1, tag = '0') => ({ debit: String(quantity * 550), intrinsic: String(quantity * 550), pnl: tag,
  premium_roi: '0', break_even: '110.5', exercise_cash: String(quantity * 10500), stock_pnl: String(quantity * 1050), stock_price_roi: '0.105', delta_shares: null });
function row(i = 0) {
  const result = { contract: { id: `call-${i}`, provider_symbol: `O:SYNTHETIC${i}`, underlying: 'SOFI', right: 'call', strike: String(105 + i),
    expiry: i % 2 ? '2028-04-14' : '2028-01-21', currency: 'USD', shares: 100, premium_multiplier: '100', exercise_style: 'american', settlement: 'physical', terms_fingerprint: `terms-${i}`, evidence: { synthetic: true } },
    terms_fingerprint: `terms-${i}`, observation: { contract: { id: `call-${i}` }, quote: { bid: '5.25', ask: '5.5', bid_size: 1, ask_size: 1,
      source_ns: ns(deadline - 60000), received_ns: ns(deadline - 59000), size_unit: 'contracts', feed_mode: 'realtime', feed_lag_s: '0', source: 'Synthetic options' } },
    assessment: { algebra_eligible: true, comparison_eligible: true, liquidity_status: 'displayed', reasons: [], expiry_ns: ns(deadline) },
    stock_reference: { ticker: 'SOFI', price: '100', price_kind: 'bid', source_ns: ns(deadline - 60000), feed_mode: 'realtime' },
    underlying: { ticker: 'SOFI', indicators: {}, facts: {}, coverage: {}, sources: ['synthetic'], as_of: '2026-10-14' },
    break_even: String(110.5 + i), be_rise: String(.105 + i / 100), spread: '0.045454545454', ask_to_bid_loss: '0.045454545454',
    scenarios: [{ terminal: '110.5', result: expiryResult() }], greeks: { delta: i ? '0' : null, iv: null, raw: {}, units_evidence: { delta: 'fraction', iv: 'fraction' }, model_evidence: { reference: 'synthetic' }, source_ns: ns(deadline - 60000) },
    open_interest: i ? 0 : null, open_interest_basis: null, filter_status: 'passed', filter_missing: [], filter_rejected: [], filter_fingerprint: 'synthetic', comparison_scope_quantity: 1 };
  result.observation = {...result.observation, contract:result.contract, greeks:result.greeks, open_interest:result.open_interest, open_interest_basis:result.open_interest_basis,reference_source_ns:null};
  return result;
}
const candles = Array.from({ length: 70 }, (_, i) => ({ time: 1760000000 + i * 86400, open: 90 + i / 10, high: 92 + i / 10, low: 89 + i / 10, close: 91 + i / 10 }));
const summary = { baseURL: base.href, browser: 'Browser plugin not available; bundled regular Playwright', viewports: [], checks: [], screenshots: [], requests: [], scenarioRequests: [], expectedConsole: [], errors: [] };
let server, browser, currentPage;
async function serve() {
  if (!args['serve-root']) return;
  const root = path.resolve(args['serve-root']);
  server = http.createServer((request, response) => {
    const name = decodeURIComponent(new URL(request.url, base).pathname);
    const file = path.resolve(root, '.' + (name === '/' ? '/index.html' : name));
    if (!file.startsWith(root + path.sep)) { response.writeHead(403); response.end(); return; }
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404); response.end(); return; }
    response.setHeader('Content-Type', file.endsWith('.html') ? 'text/html; charset=utf-8' : file.endsWith('.js') ? 'application/javascript; charset=utf-8' : 'application/octet-stream');
    fs.createReadStream(file).pipe(response);
  });
  await new Promise(resolve => server.listen(Number(base.port || 80), base.hostname, resolve));
}
async function main() {
  await serve();
  browser = await chromium.launch({ headless: true, executablePath: args['browser-executable'] || process.env.ALPHA_CHROMIUM_EXECUTABLE });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage(); currentPage = page; page.setDefaultTimeout(12000);
    const state = { admin: true, capabilities: policy(), run: 'run-A', snapshot: 'a'.repeat(64), activeRun: 'run-B', status: 'running', resultDelay: 0,
      slowScenario: null, scenarioDelay: 0, scenario409: false, result403: false, sortDelay: null, latestRun: 'run-B', tabs: null, chart403: false, posts: [], reads: [], capabilityRequests: 0, capabilityHold: true, capabilityReleases: [] };
    const errors = [], expectedHttp = new Set();
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      if (!['error', 'warning'].includes(message.type())) return;
      const text = message.text(), url = message.location().url;
      if (text === 'cdn.tailwindcss.com should not be used in production. To use Tailwind CSS in production, install it as a PostCSS plugin or use the Tailwind CLI: https://tailwindcss.com/docs/installation'
          || (/^Failed to load resource: the server responded with a status of (403|409) /.test(text) && expectedHttp.has(url))) summary.expectedConsole.push({viewport, text, url});
      else errors.push(text);
    });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) { errors.push('Unexpected external request: ' + url.origin); await route.abort(); return; }
      if (!url.pathname.startsWith('/api/')) { if (url.pathname.endsWith('favicon.ico')) await route.fulfill({ status: 204 }); else await route.continue(); return; }
      summary.requests.push({ viewport, method: request.method(), path: url.pathname });
      const respond = (data, status = 200) => { if([403,409].includes(status)) expectedHttp.add(url.href); return route.fulfill({ status, json: data }).catch(() => {}); };
      if (url.pathname === '/api/auth/me') return respond({ user: { email: 'synthetic@example.invalid', name: 'Synthetic QA', is_admin: state.admin, plan: 'pro' }, limits: { allowed_tabs: state.tabs, has_sidebar_detail: true } });
      if (url.pathname === '/api/leaps/capabilities') {
        ++state.capabilityRequests; const reply = structuredClone(state.capabilities);
        if (state.capabilityHold) await new Promise(resolve => state.capabilityReleases.push(resolve));
        return respond(reply);
      }
      if (url.pathname === '/api/leaps/status') return respond({ run_id: state.activeRun, status: state.status, automatic_wait:state.automaticWait===true,terminal:false,resumable:state.status==='paused',restartable:!['queued','running'].includes(state.status),market_date:'2026-10-14',filters:{},progress: { completed: 3, total: 22, phase: 'chain' }, reasons: [], last_complete_run_id: state.run, worker_error: null });
      if (url.pathname === '/api/leaps/results') {
        const query = Object.fromEntries(url.searchParams); state.reads.push(query);
        const saved = { run_id: query.view === 'latest' ? state.latestRun : state.run, snapshot_id: state.snapshot, status: query.view === 'latest' ? 'partial' : 'complete', rows: Array.from({ length: 22 }, (_, i) => row(i)), total: 22, next_offset: null, coverage: { eligible: 22 }, reasons: [], sort_by: query.sort_by, order: query.order };
        // Synthetic response is already globally sorted/paginated by the fixture server.
        if (query.sort_by === 'expiry') saved.rows.sort((a, b) => a.contract.expiry.localeCompare(b.contract.expiry) || a.contract.id.localeCompare(b.contract.id));
        else if (query.order === 'desc') saved.rows.reverse();
        const offset = Number(query.offset); saved.rows = saved.rows.slice(offset, offset + Number(query.limit)); saved.next_offset = offset + saved.rows.length < 22 ? offset + saved.rows.length : null;
        const denied = state.result403; const delay = state.sortDelay === query.sort_by ? 700 : state.resultDelay;
        if (delay) await pause(delay);
        return denied ? respond({ detail: { reason: 'display_rights_unverified' } }, 403) : respond(saved);
      }
      if (url.pathname === '/api/leaps/scenarios') {
        const body = request.postDataJSON(); state.posts.push(body);
        summary.scenarioRequests.push({viewport,body,stale:state.scenario409});
        if (Object.hasOwn(body,'terminal_prices')) { assert.equal(body.terminal_prices.length,1); assert.equal(typeof body.terminal_prices[0],'string'); }
        const old = body.contract_id === state.slowScenario || state.scenarioDelay || body.terminal_prices?.[0] === state.slowPrice;
        const result = { ...body, hypothetical: true, fees: 'unknown', quantity_is_fill_evidence: false, comparison_scope_quantity: 1,
          assessment: row().assessment, requested_size_liquidity: 'unverified', option_depth_unit: 'contracts', stock_depth_unit: 'shares',
          stock_reference_kind: 'bid', stock_reference_is_executable_buy: false, quote_source_ns: row().observation.quote.source_ns,
          scenarios: [{ terminal: body.terminal_prices?.[0] ?? '110.5', result: expiryResult(body.quantity, old ? '999' : '0') }] };
        const stale = state.scenario409;
        if (old) await pause(700);
        return stale ? respond({ detail: { reason: 'stale_selection' } }, 409) : respond(result);
      }
      if (url.pathname === '/api/leaps/control' || url.pathname === '/api/leaps/scan') {
        const body = request.postDataJSON(); state.posts.push(body);
        assert.equal(state.admin, true); if (body.action) assert.equal(body.run_id, state.activeRun);
        state.status = body.action === 'pause' ? 'paused' : 'queued'; state.automaticWait=false; const acknowledgment = {run_id:state.activeRun,status:state.status}, rejected=state.holdControlError;
        if (state.holdControl) await new Promise(resolve => { state.releaseControl=resolve; });
        return rejected ? respond({detail:{reason:'old_control_error'}},409) : respond(acknowledgment);
      }
      if (url.pathname === '/api/chart-data') { assert.equal(url.searchParams.get('ticker'), 'SOFI'); assert.equal(url.searchParams.get('overlays'), ''); return state.chart403 ? respond({detail:'forbidden'},403) : respond({ ticker: 'SOFI', timeframe: '1D', candles, source: 'Synthetic daily candles', chart_as_of: 1765961600, chart_requested_at: 1765961600, completed_bar_count: 69, chart_freshness: { stale: false, last_candle_time: '2025-12-17T00:00:00Z' } }); }
      if (['/api/auth/personal-positions', '/api/ticker-detail'].includes(url.pathname)) { errors.push('Forbidden generic detail effect: ' + url.pathname); return respond({}); }
      const common = { '/api/health': { status: 'ok' }, '/api/system-health': {}, '/api/scan-status': { scans: {} }, '/api/strategies': { strategies: { 'Momentum Breakout Long': { display_group: 'Synthetic' } } }, '/api/scan-results': { data: [], cached_at: '2026-10-14T10:00:00Z', scan_run_id: 'synthetic-stock', scan_status: 'complete' }, '/api/trade-reminders': { reminders: [] }, '/api/auth/public-plans': { plans: [] }, '/api/auth/legal-info': {} };
      if (Object.hasOwn(common, url.pathname)) return respond(common[url.pathname]);
      errors.push('Unexpected fixture API: ' + url.pathname); return respond({});
    });
    const navigateMenu = async name => {
      if (viewport.width < 600) { await page.getByRole('button', { name: 'Navigation öffnen', exact: true }).click(); await page.locator('.mobile-nav-sheet').getByRole('button', { name: new RegExp('^' + name + '(?:\\s|$)') }).first().click(); }
      else { await page.locator('.mega-nav').getByRole('button', { name: /^Aktien/ }).hover(); await page.locator('.mega-panel-dropdown').waitFor(); const item = page.locator('.mega-panel-dropdown').getByRole('button', { name: new RegExp('^' + name + '(?:\\s|$)') }); assert.equal(await item.count(), 1, `${name} navigation must be explicitly available`); await item.click(); await page.mouse.move(1000, 400); }
    };
    if (!['chart','custom'].includes(args['review-case'])) {
      const initialTime = Date.now(); await page.clock.install({time:initialTime}); await page.clock.pauseAt(initialTime);
      await page.goto(base.href, { waitUntil: 'domcontentloaded' });
      while (!state.capabilityRequests) await pause(10);
      await page.clock.fastForward(15000); await pause(50);
      assert.equal(state.capabilityRequests,1,'valid slow capability request must survive the 15s polling boundary without overlap/cancellation');
      await page.clock.fastForward(1000); state.capabilityHold=false; state.capabilityReleases.shift()();
      await page.locator('.mobile-nav-item-name').filter({hasText:/^LEAPS$/}).waitFor({state:'attached'}); await page.clock.resume();
      summary.checks.push({viewport,heldCapabilityAt16s:true});
    } else { state.capabilityHold=false; await page.goto(base.href,{waitUntil:'networkidle'}); }
    assert.match(await page.title(), /Alpha Station/); assert.equal(new URL(page.url()).origin, base.origin);
    await navigateMenu('LEAPS');
    const scanner = page.getByRole('region', { name: 'LEAPS Scanner', exact: true });
    await scanner.getByRole('table', { name: 'LEAPS Kontrakte' }).waitFor();
    assert.equal(state.reads[0].sort_by, 'extrinsic_annual_fraction', 'initial ranking uses server annual extrinsic');
    assert.equal(await scanner.getByRole('progressbar').count(), 1);
    assert.equal(await scanner.locator('details[open]').count(), 0);
    assert.match(await scanner.innerText(), /Letzter vollständiger Ergebnislauf run-A/);
    assert.equal(await page.locator('#boot-error-overlay').count(), 0);
    assert.ok((await page.locator('#root').innerText()).length > 100);
    assert.ok(!/WATCH|JETZT kaufen|JETZT_TRADEN/.test(await scanner.innerText()));
    state.status='paused'; state.automaticWait=true;
    await scanner.getByRole('button',{name:'Neu laden',exact:true}).click();
    await scanner.getByRole('button',{name:'Pause',exact:true}).click();
    assert.equal(state.posts.at(-1).action,'pause');
    // The POST precedes the following status response/React commit. Wait for
    // the actual disabled state, not merely the already-visible button.
    await page.waitForFunction(button => button.disabled,
      await scanner.getByRole('button',{name:'Pause',exact:true}).elementHandle());
    assert.equal(await scanner.getByRole('button',{name:'Pause',exact:true}).isDisabled(),true);
    state.status='running'; await scanner.getByRole('button',{name:'Neu laden',exact:true}).click();
    const screenshot = async label => { const file = path.resolve(args.output, `${viewport.width}-${label}.png`); await page.screenshot({ path: file, fullPage: false }); summary.screenshots.push(file); };
    await screenshot('saved-results');
    const open = async id => { await scanner.getByRole('button', { name: `Basiswertchart ${id}`, exact: true }).click(); return page.getByRole('complementary', { name: 'LEAPS-Kontrakt' }); };
    let detail = await open('call-0'); await detail.locator('canvas').first().waitFor(); await detail.getByText(/Debit \(1 Kontrakte\)/).waitFor();
    assert.ok((await detail.getByRole('region', { name: 'Basiswertchart' }).boundingBox()).y < (await detail.getByRole('region', { name: 'Kontraktvergleich' }).boundingBox()).y);
    assert.match(await detail.innerText(), /Aktienreferenz \(BID\)/); assert.match(await detail.innerText(), /Optionsprämie \(ASK\)/); assert.equal(await detail.locator('details[open]').count(), 0);
    assert.ok(!/ENTRY|STOP|TradeHealth|Wyckoff|Reminder/.test(await detail.innerText()));
    await screenshot('chart-first');
    assert.equal(await detail.getByLabel('Eigener Verfallskurs USD',{exact:true}).count(),1,'freely chosen absolute expiry price must be available');
    await detail.getByText('Eigener Verfallskurs (optional)',{exact:true}).click();
    const customPrice=detail.getByLabel('Eigener Verfallskurs USD',{exact:true});
    await customPrice.fill('123.456'); await detail.getByRole('cell',{name:'$123.46',exact:true}).waitFor(); assert.deepEqual(state.posts.at(-1).terminal_prices,['123.456']);
    await customPrice.fill('0'); await detail.getByRole('cell',{name:'$0.00',exact:true}).waitFor(); assert.deepEqual(state.posts.at(-1).terminal_prices,['0']);
    await detail.getByLabel('Kontraktanzahl',{exact:true}).fill('2'); await detail.getByText(/Debit \(2 Kontrakte\)/).waitFor(); assert.deepEqual(state.posts.at(-1).terminal_prices,['0']);
    const beforeInvalid=state.posts.length; await customPrice.fill('-1'); await detail.getByText(/Nichtnegativen USD-Verfallskurs/).waitFor(); await pause(100); assert.equal(state.posts.length,beforeInvalid,'invalid custom input must not become a fake zero request');
    state.slowPrice='150'; await customPrice.fill('150'); await pause(70); await customPrice.fill('175'); await detail.getByRole('cell',{name:'$175.00',exact:true}).waitFor(); await pause(800); assert.ok(!/999\.00/.test(await detail.innerText()),'old custom-price reply must not replace new scenario');
    await screenshot('custom-expiry-price');
    await customPrice.fill(''); await detail.getByRole('cell',{name:'$110.50',exact:true}).waitFor(); assert.equal(Object.hasOwn(state.posts.at(-1),'terminal_prices'),false,'empty custom field must request server defaults');
    await detail.getByLabel('Kontraktanzahl',{exact:true}).fill('1'); await detail.getByText(/Debit \(1 Kontrakte\)/).waitFor(); assert.equal(Object.hasOwn(state.posts.at(-1),'terminal_prices'),false);
    await detail.getByText('Eigener Verfallskurs (optional)',{exact:true}).click(); state.slowPrice=null;
    await detail.getByLabel('Kontraktanzahl', { exact: true }).fill('2'); await detail.getByText(/Debit \(2 Kontrakte\): \$1100.00/).waitFor();
    // Exact zero and nullable Greeks are different, and large cash remains exact.
    await detail.getByText('Kontrakt-/Daten-Details',{exact:true}).click(); assert.match(await detail.innerText(), /IV: – · Delta: –/); await screenshot('scenario-quantity');
    await detail.getByRole('button', { name: 'LEAPS schließen' }).click();
    state.slowScenario = 'call-0'; detail = await open('call-0'); await pause(70); await detail.getByRole('button', { name: 'LEAPS schließen' }).click(); detail = await open('call-1');
    await detail.getByText(/Debit \(1 Kontrakte\)/).waitFor(); await pause(800); assert.match(await detail.innerText(), /Kontrakt call-1/); assert.ok(!/999\.00/.test(await detail.innerText()));
    await detail.getByText('Kontrakt-/Daten-Details',{exact:true}).click(); assert.match(await detail.innerText(), /Delta: 0.00/);
    // Quantity and tab identities invalidate late JSON, not only HTTP headers.
    state.scenarioDelay = 700; await detail.getByLabel('Kontraktanzahl', { exact: true }).fill('3'); await pause(70); state.scenarioDelay = 0; await detail.getByLabel('Kontraktanzahl', { exact: true }).fill('2');
    await detail.getByText(/Debit \(2 Kontrakte\): \$1100.00/).waitFor(); await pause(800); assert.ok(!/999\.00/.test(await detail.innerText()));
    state.scenarioDelay = 700; await detail.getByLabel('Kontraktanzahl', { exact: true }).fill('4'); if (viewport.width < 600) await detail.getByRole('button', { name: 'LEAPS schließen' }).click(); await navigateMenu('Aktien Scanner'); await pause(800); assert.equal(await page.getByRole('complementary', { name: 'LEAPS-Kontrakt' }).count(), 0);
    state.scenarioDelay = 0; state.resultDelay = 700; await navigateMenu('LEAPS');
    await scanner.getByRole('button', { name: 'Basiswertchart call-0', exact: true }).waitFor(); assert.match(await scanner.innerText(), /wird verifiziert/); await pause(800); state.resultDelay = 0;
    // New global sort and page scopes must not display old-order responses.
    state.sortDelay = 'strike'; await scanner.getByRole('button', { name: 'Strike $', exact: true }).click(); await pause(70); await scanner.getByRole('button', { name: 'Verfall', exact: true }).click(); await pause(800);
    assert.equal(await scanner.locator('th[aria-sort="ascending"]').innerText(), 'Verfall ↑'); assert.equal(state.reads.at(-1).sort_by, 'expiry');
    await scanner.getByRole('button', { name: 'Weiter', exact: true }).click(); await scanner.getByText('21–22 / 22', { exact: true }).waitFor(); assert.equal(state.reads.at(-1).offset, '20');
    await scanner.getByRole('button', { name: 'Zurück', exact: true }).click(); await scanner.getByRole('button', { name: 'Basiswertchart call-0', exact: true }).waitFor();
    state.slowScenario = null; state.scenarioDelay = 700; detail = await open('call-0'); await detail.getByRole('button', { name: 'LEAPS schließen' }).click();
    await scanner.getByLabel('Ergebnisstand', { exact: true }).selectOption('latest'); await scanner.getByText(/Gespeichert: run-B/).waitFor(); state.scenarioDelay = 0; detail = await open('call-0'); await detail.getByText(/Ergebnislauf run-B/).waitFor(); await pause(800); assert.ok(!/999\.00/.test(await detail.innerText()));
    await detail.getByRole('button', { name: 'LEAPS schließen' }).click();
    await scanner.getByRole('button', { name: 'Pause', exact: true }).click(); await scanner.getByRole('button', { name: 'Fortsetzen', exact: true }).waitFor({ state: 'visible' });
    await scanner.getByRole('button', { name: 'Fortsetzen', exact: true }).click(); assert.equal(state.posts.filter(item => item.action).length, 3);
    // A late control acknowledgment for B cannot overwrite newly observed active C.
    state.holdControl = true; await scanner.getByRole('button',{name:'Pause',exact:true}).click();
    while (!state.releaseControl) await pause(10);
    state.activeRun='run-C'; state.status='running'; await scanner.getByText(/Aktiver Lauf: run-C/).waitFor();
    await page.evaluate(() => { window.leapsRunTexts=[]; window.leapsRunObserver=new MutationObserver(() => { const el=document.querySelector('[aria-label="LEAPS Scanner"]'); if(el) window.leapsRunTexts.push(el.innerText); }); window.leapsRunObserver.observe(document.querySelector('[aria-label="LEAPS Scanner"]'),{subtree:true,childList:true,characterData:true}); });
    state.holdControl=false; state.releaseControl(); state.releaseControl=null; await pause(800);
    const runTexts=await page.evaluate(() => {window.leapsRunObserver.disconnect();return window.leapsRunTexts;});
    assert.ok(runTexts.every(text=>!text.includes('Aktiver Lauf: run-B')), 'late run-B acknowledgment must never repaint run-C');
    state.holdControl=true; state.holdControlError=true; await scanner.getByRole('button',{name:'Pause',exact:true}).click(); while(!state.releaseControl) await pause(10);
    state.activeRun='run-D'; state.status='running'; await scanner.getByText(/Aktiver Lauf: run-D/).waitFor();
    state.holdControl=false; state.holdControlError=false; state.releaseControl(); state.releaseControl=null; await pause(800);
    assert.ok(!(await scanner.innerText()).includes('old_control_error'), 'late old-run failure must not overwrite the current run notice');
    state.scenario409 = true; detail = await open('call-0'); await detail.getByText(/Auswahl veraltet/).waitFor(); assert.equal(await detail.getByLabel('Kontraktanzahl', { exact: true }).isDisabled(), true); await screenshot('stale-selection');
    await detail.getByRole('button', { name: 'LEAPS schließen' }).click(); state.scenario409 = false;
    // Expire without receiving a new quote; remount cannot renew original expiry.
    await page.clock.install({ time: deadline + 1000 }); detail = await open('call-0'); await detail.getByTestId('leaps-validity').getByText(/Datiert/).waitFor(); await detail.getByRole('button', { name: 'LEAPS schließen' }).click();
    detail = await open('call-0'); assert.match(await detail.getByTestId('leaps-validity').innerText(), /Datiert/); await screenshot('dated-quote');
    await detail.getByRole('button', { name: 'LEAPS schließen' }).click();
    state.result403 = true; await scanner.getByRole('button', { name: 'Neu laden', exact: true }).click(); await scanner.waitFor({ state: 'detached' }); assert.equal(await page.getByRole('complementary', { name: 'LEAPS-Kontrakt' }).count(), 0);
    // Fresh login with null legacy tabs cannot grant LEAPS by itself.
    state.result403 = false; state.capabilities = { ...policy(false), enabled: false }; state.admin = false; await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Navigation öffnen', exact: true }).count(); assert.equal(await page.locator('.mega-item-name,.mobile-nav-item-name').filter({ hasText: /^LEAPS$/ }).count(), 0);
    state.capabilities = policy(false); await page.reload({ waitUntil: 'networkidle' }); await navigateMenu('LEAPS'); await scanner.getByRole('table', { name: 'LEAPS Kontrakte' }).waitFor();
    for (const name of ['Start', 'Pause', 'Fortsetzen']) assert.equal(await scanner.getByRole('button', { name, exact: true }).count(), 0, 'admin controls are absent for readers');
    assert.equal(await scanner.getByRole('group', { name: 'Optionale Filter für einen neuen Lauf' }).count(), 0);
    assert.ok(!/Nur Admin|Aktiver Lauf:|Gespeichert: run-|Ergebnislauf run-/.test(await scanner.innerText()), 'reader status hides raw run metadata');
    state.tabs = ['scanner']; state.capabilities.endpoint_capabilities.scenarios = false; await page.reload({ waitUntil: 'networkidle' }); await navigateMenu('LEAPS');
    const chartRequests = summary.requests.filter(item => item.path === '/api/chart-data').length;
    detail = await open('call-0'); await detail.getByText(/Chart-Zugang nicht freigegeben/).waitFor(); assert.match(await detail.innerText(), /Szenarien nicht freigegeben/); assert.equal(await detail.getByLabel('Kontraktanzahl', {exact:true}).isDisabled(),true);
    assert.equal(summary.requests.filter(item => item.path === '/api/chart-data').length,chartRequests); await screenshot('separate-chart-scenario-rights');
    await detail.getByRole('button',{name:'LEAPS schließen'}).click();
    if (args['review-case'] !== 'capabilities') {
      state.chart403=false;
      // Mount the REAL shipped component at its public prop boundary, not a mock
      // or React-internals mutation. Existing full-App flow remains above.
      const selected={kind:'leaps',ticker:'SOFI',run_id:'run-A',snapshot_id:state.snapshot,row:row()};
      await page.evaluate(({selection,access})=>{
        const host=document.createElement('div'); host.id='leaps-fixture-host';document.body.appendChild(host);
        function ChartRightsFixture(){const [allowed,setAllowed]=React.useState(true);return React.createElement(React.Fragment,null,
          React.createElement('button',{id:'leaps-fixture-revoke',onClick:()=>setAllowed(false)},'Revoke chart'),
          React.createElement('button',{id:'leaps-fixture-grant',onClick:()=>setAllowed(true)},'Grant chart'),
          React.createElement(LeapsContractDetail,{selection,policy:access,chartAllowed:allowed,onClose:()=>{},onDenied:()=>{throw new Error('unexpected fixture access denial');}}));}
        ReactDOM.render(React.createElement(ChartRightsFixture),host);
      },{selection:selected,access:policy(false)});
      const fixture=page.locator('#leaps-fixture-host'), fixtureDetail=fixture.getByRole('complementary',{name:'LEAPS-Kontrakt'});
      await fixtureDetail.locator('canvas').first().waitFor();
      await fixtureDetail.evaluate(el=>{window.leapsSameDetail=el;window.leapsPriorCanvas=el.querySelector('canvas');});
      const beforeGrantRequests=summary.requests.filter(item=>item.path==='/api/chart-data').length;
      await page.evaluate(()=>document.getElementById('leaps-fixture-revoke').click());
      await fixtureDetail.getByText(/Chart-Zugang nicht freigegeben/).waitFor();
      assert.equal(await fixtureDetail.locator('canvas').count(),0,'revoked same-mounted chart must dispose all candles');
      assert.equal(await fixtureDetail.evaluate(el=>el===window.leapsSameDetail),true,'detail identity must survive entitlement change');
      assert.ok(!(await fixtureDetail.innerText()).includes('Synthetic daily candles'),'revoked chart source metadata must clear');
      await screenshot('chart-rights-revoked');
      await page.evaluate(()=>document.getElementById('leaps-fixture-grant').click());
      await fixtureDetail.locator('canvas').first().waitFor();
      assert.equal(await fixtureDetail.evaluate(el=>el===window.leapsSameDetail&&el.querySelector('canvas')!==window.leapsPriorCanvas),true);
      assert.equal(summary.requests.filter(item=>item.path==='/api/chart-data').length,beforeGrantRequests+1,'regrant must fetch fresh authorized candles');
      await screenshot('chart-rights-regranted');
      await page.evaluate(()=>{const host=document.getElementById('leaps-fixture-host');ReactDOM.unmountComponentAtNode(host);host.remove();});
      summary.checks.push({viewport,sameMountedChartTrueFalseTrue:true});
    }
    state.tabs = null; state.capabilities = policy(false); state.chart403 = true; await page.reload({waitUntil:'networkidle'}); await navigateMenu('LEAPS'); detail = await open('call-0'); await detail.getByText('Chart-Zugang nicht freigegeben', {exact:true}).waitFor();
    await detail.getByRole('button',{name:'LEAPS schließen'}).click();
    if (args['review-case'] !== 'chart' && args['review-case'] !== 'custom') {
      detail=await open('call-0'); await detail.getByText('Chart-Zugang nicht freigegeben',{exact:true}).waitFor();
      state.capabilityHold=true; const beforeRenewal=state.capabilityRequests;
      // fastForward works with the running clock. pauseAt(Date.now()) races
      // the browser protocol and can try to move the clock into the past.
      await page.clock.fastForward(15000);
      while(!state.capabilityReleases.length) await pause(10);
      assert.equal(state.capabilityRequests,beforeRenewal+1,'one renewal request only');
      await page.clock.fastForward(20001); await scanner.waitFor({state:'detached'});
      assert.equal(await page.getByRole('complementary',{name:'LEAPS-Kontrakt'}).count(),0,'timed-out rights renewal must remove prior granted detail');
      assert.equal(await page.locator('.mega-item-name,.mobile-nav-item-name').filter({hasText:/^LEAPS$/}).count(),0,'timed-out renewal must fail closed');
      state.capabilityHold=false; state.capabilityReleases.splice(0).forEach(release=>release()); await pause(100);
      assert.equal(await page.locator('.mega-item-name,.mobile-nav-item-name').filter({hasText:/^LEAPS$/}).count(),0,'late expired capability response must not restore old grant');
      await screenshot('capability-renewal-timeout');
      await page.clock.fastForward(15000); await page.locator('.mobile-nav-item-name').filter({hasText:/^LEAPS$/}).waitFor({state:'attached'}); await page.clock.resume();
      summary.checks.push({viewport,capabilityRenewalTimeoutFailClosed:true});
    }
    summary.viewports.push(viewport); summary.checks.push({ viewport, identity: true, notBlank: true, overlayAbsent: true, chartFirst: true, savedCache: true, controls: true, contractQuantityTabRunSortPageRaces: true, expiryOriginal: true, rights: true });
    assert.deepEqual(errors, [], 'console/pageerror/network health'); await context.close();
  }
}
main().catch(async error => { summary.errors.push(error.stack || String(error)); process.exitCode = 1; if(currentPage && !currentPage.isClosed()) { await currentPage.screenshot({path:path.join(args.output,'failure.png')}); fs.writeFileSync(path.join(args.output,'failure-dom.txt'),await currentPage.locator('body').innerText()); } }).finally(async () => {
  if (browser) await browser.close(); if (server) await new Promise(resolve => server.close(resolve));
  fs.writeFileSync(path.join(args.output, 'results.json'), JSON.stringify(summary, null, 2)); console.log(JSON.stringify({ ...summary, requests: summary.requests.length,scenarioRequests:summary.scenarioRequests.length,expectedConsole:summary.expectedConsole.length }, null, 2));
});
