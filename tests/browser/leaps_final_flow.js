"use strict";
// Real shipped React components + public DTOs exported by actual clean backend.
// Only HTTP boundary is synthetic. No provider, account, SMTP or external IO.
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const args=Object.fromEntries(process.argv.slice(2).reduce((pairs,arg,i,list)=>arg.startsWith('--')?[...pairs,[arg.slice(2),list[i+1]]]:pairs,[]));
const fixture=JSON.parse(fs.readFileSync(args.fixture,'utf8'));
const root=path.resolve(args['serve-root']),base=new URL(args['base-url']||'http://127.0.0.1:8092');
assert.equal(base.hostname,'127.0.0.1'); assert.ok(!fs.existsSync(args.output));fs.mkdirSync(args.output,{recursive:true});
const {chromium}=require(args['playwright-module']);
const summary={fixtureBoundary:fixture.fixture_boundary,viewports:[],screenshots:[],checks:[],posts:[],warnings:[],errors:[]};
const policy={enabled:true,admin:true,results_allowed:true,display_rights_verified:true,
    endpoint_capabilities:{results:true,status:true,scenarios:true,scan:true,control:true},
    capabilities:{reference:true,option_quotes:true,stock_quotes:true,standard_terms:true,sessions:true,budget:true}};
const candles=Array.from({length:30},(_,i)=>({time:1760000000+i*86400,open:20,high:21,low:19,close:20}));
let browser,page,server;
async function main(){
    server=http.createServer((req,res)=>{const name=new URL(req.url,base).pathname,file=path.resolve(root,'.'+(name==='/'?'/index.html':name));
        if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}
        res.setHeader('Content-Type',file.endsWith('.html')?'text/html; charset=utf-8':'application/javascript; charset=utf-8');fs.createReadStream(file).pipe(res);});
    await new Promise(resolve=>server.listen(Number(base.port),base.hostname,resolve));
    browser=await chromium.launch({headless:true,executablePath:args['browser-executable']});
    for(const viewport of [{width:1440,height:900},{width:390,height:844}]){
        const context=await browser.newContext({viewport});page=await context.newPage();page.setDefaultTimeout(12000);
        const state={kind:'supported',status:fixture.recovery.terminal_partial};
        page.on('pageerror',e=>summary.errors.push(e.message));
        page.on('console',m=>{if(['warning','error'].includes(m.type())){if(m.text().startsWith('cdn.tailwindcss.com should not be used in production.'))summary.warnings.push(m.text());else summary.errors.push(m.text());}});
        await context.route('**/*',async route=>{
            const req=route.request(),url=new URL(req.url());
            if(url.hostname!=='127.0.0.1'){summary.errors.push('External request blocked');await route.abort();return;}
            const respond=data=>route.fulfill({json:data});
            if(url.pathname==='/favicon.ico')return route.fulfill({status:204});
            if(!url.pathname.startsWith('/api/'))return route.continue();
            const current=fixture.cases[state.kind];
            if(url.pathname==='/api/leaps/capabilities')return respond(policy);
            if(url.pathname==='/api/leaps/status')return respond(state.status);
            if(url.pathname==='/api/leaps/results')return respond({...current.results,sort_by:url.searchParams.get('sort_by'),order:url.searchParams.get('order')});
            if(url.pathname==='/api/leaps/scenarios'){
                const body=req.postDataJSON();summary.posts.push(body);
                assert.equal(body.run_id,current.results.run_id);assert.equal(body.snapshot_id,current.results.snapshot_id);
                assert.equal(body.contract_id,current.results.rows[0].contract.id);
                return respond(current.scenarios[String(body.quantity)]);
            }
            if(url.pathname==='/api/leaps/scan'){
                const body=req.postDataJSON();summary.posts.push(body);
                assert.deepEqual(body,{filters:{max_be_rise_percent:'25.001',min_maturity_months:'15',max_maturity_months:'18',min_underlying_price:'10.125',close_above_ema200:true}});
                state.status=fixture.filtered_status;return respond({run_id:state.status.run_id,status:'queued'});
            }
            if(url.pathname==='/api/leaps/control'){
                const body=req.postDataJSON();summary.posts.push(body);
                assert.deepEqual(body,{run_id:fixture.recovery.soft_partial.run_id,action:'resume'});
                state.status={...state.status,status:'queued',resumable:false,restartable:false};return respond({run_id:state.status.run_id,status:'queued'});
            }
            if(url.pathname==='/api/chart-data')return respond({ticker:'SOFI',timeframe:'1D',candles,source:'Synthetic completed candles',chart_freshness:{last_candle_time:'2026-10-12'}});
            const common={'/api/auth/me':{user:{email:'synthetic@example.invalid',is_admin:true,plan:'pro'},limits:{allowed_tabs:null,has_sidebar_detail:true}},
                '/api/health':{status:'ok'},'/api/system-health':{},'/api/scan-status':{scans:{}},'/api/strategies':{strategies:{}},
                '/api/scan-results':{data:[],scan_status:'complete'},'/api/trade-reminders':{reminders:[]},'/api/auth/public-plans':{plans:[]},'/api/auth/legal-info':{}};
            if(Object.hasOwn(common,url.pathname))return respond(common[url.pathname]);
            summary.errors.push('Unexpected API '+url.pathname);return respond({});
        });
        await page.goto(base.href,{waitUntil:'networkidle'});
        assert.match(await page.title(),/Alpha Station/);assert.equal(new URL(page.url()).origin,base.origin);
        // Public prop boundary only, never React internals or a replacement UI.
        await page.evaluate(access=>{
            const host=document.getElementById('root');ReactDOM.unmountComponentAtNode(host);
            function Fixture(){const [selected,setSelected]=React.useState(null);const [cache,setCache]=React.useState({});
                return React.createElement(React.Fragment,null,
                    React.createElement(LeapsTab,{policy:access,account:'synthetic',scanCache:cache,updateScanCache:(key,value)=>setCache(old=>({...old,[key]:value})),onSelectTicker:setSelected,onInvalidate:()=>setSelected(null),onDenied:()=>{throw new Error('unexpected denial');}}),
                    selected&&React.createElement(LeapsContractDetail,{selection:selected,policy:access,chartAllowed:true,onClose:()=>setSelected(null),onDenied:()=>{throw new Error('unexpected detail denial');}}));}
            ReactDOM.render(React.createElement(Fixture),host);
        },policy);
        const scanner=page.getByRole('region',{name:'LEAPS Scanner',exact:true});
        await scanner.getByText('Synthetic Verified Company',{exact:true}).waitFor();
        assert.equal(await scanner.getByRole('progressbar').count(),1);assert.equal(await page.locator('#boot-error-overlay').count(),0);
        assert.match(await scanner.innerText(),/465 Tage ab 2026-10-13/);
        assert.equal(await scanner.getByRole('button',{name:'Start',exact:true}).isEnabled(),true);
        assert.equal(await scanner.getByRole('button',{name:'Fortsetzen',exact:true}).isDisabled(),true);
        const shot=async name=>{const file=path.resolve(args.output,viewport.width+'-'+name+'.png');await page.screenshot({path:file,fullPage:false});summary.screenshots.push(file);};
        const open=async()=>{await scanner.getByRole('button',{name:/^Basiswertchart /}).click();const detail=page.getByRole('complementary',{name:'LEAPS-Kontrakt'});await detail.getByText(/Debit \(1 Kontrakte\)/).waitFor();return detail;};
        let detail=await open();await detail.locator('canvas').first().waitFor();
        assert.equal(await detail.locator('details[open]').count(),0);
        assert.ok((await detail.getByRole('region',{name:'Basiswertchart'}).boundingBox()).y<(await detail.getByRole('region',{name:'Kontraktvergleich'}).boundingBox()).y);
        await detail.getByLabel('Kontraktanzahl',{exact:true}).fill('3');await detail.getByText(/Debit \(3 Kontrakte\)/).waitFor();
        assert.match(await detail.getByTestId('leaps-stock-capital').innerText(),/\$6000\.00.*300 Aktien/);
        await detail.getByText('Kontrakt-/Daten-Details',{exact:true}).click();assert.match(await detail.innerText(),/Physische Ausübung/);assert.match(await detail.innerText(),/keine identische Delta-Exposure/);
        await detail.getByText('Basiswert-Evidenz · Technik, SEC und separate Bewertung',{exact:true}).click();
        assert.match(await detail.innerText(),/220 abgeschlossene Kerzen/);assert.match(await detail.innerText(),/operating_company/);assert.match(await detail.innerText(),/SEC-Unternehmensqualität/);
        await detail.getByText('Separate Bewertung',{exact:true}).scrollIntoViewIfNeeded();await shot('supported-evidence-capital');
        await detail.getByRole('button',{name:'LEAPS schließen'}).click();
        for(const kind of ['bank','missing','excluded']){
            state.kind=kind;await scanner.getByRole('button',{name:'Neu laden',exact:true}).click();await scanner.getByText(new RegExp('Gespeichert: fixture-'+kind)).waitFor();
            assert.match(await scanner.innerText(),kind==='excluded'?/Filter ausgeschlossen/:/Filter nicht prüfbar/);
            detail=await open();
            if(kind==='bank'){
                await detail.getByText('Basiswert-Evidenz · Technik, SEC und separate Bewertung',{exact:true}).click();
                await detail.getByText(/Bank\/REIT: Spezialdaten/).waitFor();assert.match(await detail.innerText(),/missing_completed_history/);
                await detail.getByText(/Bank\/REIT: Spezialdaten/).scrollIntoViewIfNeeded();await shot('bank-missing-history');
            }
            if(kind==='missing'){
                await detail.getByLabel('Kontraktanzahl',{exact:true}).fill('3');await detail.getByText(/Debit \(3 Kontrakte\)/).waitFor();
                assert.match(await detail.getByTestId('leaps-stock-capital').innerText(),/\$–.*300 Aktien/);await shot('missing-stock-reference');
            }
            await detail.getByRole('button',{name:'LEAPS schließen'}).click();
        }
        state.status=fixture.recovery.stale_paused;await scanner.getByRole('button',{name:'Neu laden',exact:true}).click();await scanner.getByText(/Aktiver Lauf: recovery-stale_paused/).waitFor();
        assert.equal(await scanner.getByRole('button',{name:'Start',exact:true}).isEnabled(),true);assert.equal(await scanner.getByRole('button',{name:'Fortsetzen',exact:true}).isDisabled(),true);
        state.status=fixture.recovery.soft_partial;await scanner.getByRole('button',{name:'Neu laden',exact:true}).click();await scanner.getByText(/Aktiver Lauf: recovery-soft_partial/).waitFor();
        await scanner.getByRole('button',{name:'Fortsetzen',exact:true}).click();await scanner.getByText(/· queued ·/).first().waitFor();
        state.status=fixture.recovery.terminal_partial;await scanner.getByRole('button',{name:'Neu laden',exact:true}).click();await scanner.getByText(/Aktiver Lauf: recovery-terminal_partial/).waitFor();
        for(const [label,value] of [['Max. BE-Anstieg %','25.001'],['Min. Laufzeit Monate (12–24)','15'],['Max. Laufzeit Monate (12–24)','18'],['Min. Aktienreferenz USD','10.125']])await scanner.getByLabel(label,{exact:true}).fill(value);
        await scanner.getByLabel('Abgeschlossener Tageskurs über EMA200 (gleiche Tagesbasis)',{exact:true}).check();
        assert.match(await scanner.getByTestId('leaps-active-filters').innerText(),/Keine optionalen Filter/);
        await scanner.getByRole('button',{name:'Start',exact:true}).click();await scanner.getByText(/· queued ·/).first().waitFor();
        assert.match(await scanner.getByTestId('leaps-active-filters').innerText(),/Max\. BE-Anstieg %: 25\.00%/);
        await scanner.getByLabel('Max. BE-Anstieg %',{exact:true}).fill('99');assert.ok(!(await scanner.getByTestId('leaps-active-filters').innerText()).includes('99'));
        await scanner.getByTestId('leaps-active-filters').scrollIntoViewIfNeeded();await shot('immutable-filter-criteria');
        summary.viewports.push(viewport);summary.checks.push({viewport,backendDTO:true,terminalRestart:true,staleRestart:true,softResume:true,quantityAndMissingCapital:true,evidenceSupportedAndPartial:true,companyAndDatedTerm:true,filterSubmissionAndBinding:true});
        await context.close();
    }
    assert.deepEqual(summary.errors,[]);
}
main().catch(async error=>{summary.errors.push(error.stack||String(error));process.exitCode=1;if(page&&!page.isClosed()){await page.screenshot({path:path.join(args.output,'failure.png')});fs.writeFileSync(path.join(args.output,'failure-dom.txt'),await page.locator('body').innerText());}}).finally(async()=>{
    if(browser)await browser.close();if(server)await new Promise(resolve=>server.close(resolve));fs.writeFileSync(path.join(args.output,'results.json'),JSON.stringify(summary,null,2));console.log(JSON.stringify({...summary,posts:summary.posts.length,warnings:summary.warnings.length},null,2));
});
