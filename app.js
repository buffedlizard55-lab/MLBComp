/* MLBComp static competition client.  It renders committed JSON projections only. */
const DATA_FILES = ['summary','leaderboard','strategies','upcoming_bets','open_positions','bets_ledger','research_experiments','registry','audit_checks','irregularities','kalshi_trades'];
const S = {data:{}, tab:'dashboard'};
const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const h = value => String(value ?? '—').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const arr = key => Array.isArray(S.data[key]) ? S.data[key] : [];
const summary = () => S.data.summary || {};
const fmtN = v => v == null || v === '' ? '—' : Number(v).toLocaleString(undefined,{maximumFractionDigits:2});
const fmtPct = v => v == null || v === '' ? '—' : `${Number(v).toFixed(2)}%`;
const fmtMoney = v => v == null || v === '' ? '—' : `${Number(v) < 0 ? '−' : ''}$${Math.abs(Number(v)).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;
const valueStatus = (v, suffix='') => v == null ? '<span class="muted">No data</span>' : `${h(v)}${suffix}`;
const unique = values => [...new Set(values.filter(v => v != null && v !== ''))].sort();

async function loadData() {
  const responses = await Promise.all(DATA_FILES.map(async key => {
    try { const r = await fetch(`data/${key}.json`, {cache:'no-store'}); return [key, r.ok ? await r.json() : []]; }
    catch (_) { return [key, key === 'summary' ? {data_mode:'UNAVAILABLE'} : []]; }
  }));
  S.data = Object.fromEntries(responses);
  renderAll();
}

function initTabs() {
  $$('.tabs button').forEach(button => button.addEventListener('click', () => go(button.dataset.tab)));
  $$('[data-go]').forEach(button => button.addEventListener('click', () => go(button.dataset.go)));
  const hash = location.hash.slice(1);
  if (document.getElementById(`view-${hash}`)) go(hash, false);
  addEventListener('hashchange', () => { const tab = location.hash.slice(1); if (document.getElementById(`view-${tab}`)) go(tab, false); });
}
function go(tab, updateHash=true) {
  if (!document.getElementById(`view-${tab}`)) return;
  S.tab = tab;
  $$('.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${tab}`));
  if (updateHash) history.replaceState(null,'',`#${tab}`);
}

function renderAll() {
  const m = summary();
  const mode = m.data_mode || 'UNAVAILABLE';
  $('#data-mode').textContent = mode.replaceAll('_',' ');
  $('#data-mode').className = `status ${mode === 'SOURCE_SNAPSHOT' ? 'verified' : 'warning'}`;
  $('#truth-banner').innerHTML = `<b>${h(mode.replaceAll('_',' '))}.</b> ${mode === 'SOURCE_SNAPSHOT' ? 'Only source-backed observations are displayed.' : 'No source snapshot is loaded. Strategy hypotheses and audit controls are visible; no game, price, fill, result or PnL is asserted.'} <span>Every output is paper-only.</span>`;
  $('#hero-mode').textContent = mode.replaceAll('_',' ');
  $('#hero-asof').textContent = `as of ${h(m.as_of_date || '—')}`;
  $('#footer-meta').textContent = `${fmtN(m.total_strategies)} strategy versions · ${fmtN(m.total_simulated_bets)} wager records · ${h(m.as_of_date || '')}`;
  renderDashboard(); renderFilters(); renderLeaderboard(); renderPostseason(); renderStrategies(); renderUpcoming(); renderPositions(); renderHistory(); renderAnalytics(); renderResearch(); renderSources(); renderVerification();
}

function renderDashboard() {
  const m = summary();
  const cards = [
    ['Games tracked', fmtN(m.total_games_tracked), `${fmtN(m.completed_games)} completed · ${fmtN(m.upcoming_games)} upcoming`],
    ['Strategy library', fmtN(m.total_strategies), 'versioned hypotheses'],
    ['Ledger records', fmtN(m.total_simulated_bets), 'append-only projection'],
    ['Verified PnL', m.total_simulated_pnl == null ? 'No data' : fmtMoney(m.total_simulated_pnl), 'observed prices only'],
    ['Open paper positions', fmtN(m.total_open_positions), 'no real orders'],
    ['Source mode', (m.data_mode || '—').replaceAll('_',' '), h(m.current_stage || '')],
  ];
  $('#kpis').innerHTML = cards.map(c => `<div class="kpi"><span>${h(c[0])}</span><strong>${c[1]}</strong><small>${c[2]}</small></div>`).join('');
  const audits = arr('audit_checks'); const passed = audits.filter(x => x.passed).length;
  $('#health').innerHTML = `<div class="health-row"><strong>${passed}/${audits.length || 0}</strong><span>control checks passing</span></div><div class="health-row"><strong>${fmtN(arr('registry').length)}</strong><span>sources in registry</span></div><div class="health-row"><strong>${fmtN(arr('irregularities').length)}</strong><span>issue records</span></div><p class="muted">A passing control does not mean a data source is available.</p>`;
  const env = m.environment_breakdown || {};
  $('#environment-summary').innerHTML = ['REG','POST','WC','DS','LCS','WS'].map(k => { const e=env[k]||{}; return `<div class="env-row"><span><b>${h(k)}</b> ${h(k==='REG'?'Regular season':k==='POST'?'Postseason':(k==='LCS'?'LCS':k==='WS'?'World Series':k==='DS'?'Division Series':'Wild Card'))}</span><strong>${fmtN(e.verified_bets || 0)} verified</strong><small>${h(e.status || 'NO DATA')}</small></div>`; }).join('');
}

function fillSelect(id, values, allLabel='All') {
  const select = document.getElementById(id); if (!select) return;
  const current = select.value || 'ALL';
  const first = select.options[0];
  select.innerHTML = ''; const option = document.createElement('option'); option.value='ALL'; option.textContent=allLabel; select.appendChild(option);
  values.forEach(v => { const o=document.createElement('option'); o.value=v; o.textContent=v; select.appendChild(o); });
  select.value = [...select.options].some(o=>o.value===current) ? current : 'ALL';
}
function renderFilters() {
  const lb=arr('leaderboard'), st=arr('strategies'), up=arr('upcoming_bets'), ledger=arr('bets_ledger'), src=arr('registry');
  ['leader-env','strategy-env','upcoming-env','history-env'].forEach(id => fillSelect(id, unique([...lb,...st,...up,...ledger].map(x=>x.env || x.environment))));
  fillSelect('leader-market', unique(lb.map(x=>x.market))); fillSelect('strategy-market', unique(st.map(x=>x.market))); fillSelect('upcoming-round', unique(up.map(x=>x.round_code))); fillSelect('history-status', unique(ledger.map(x=>x.status || x.event_type)));
  fillSelect('strategy-status', unique(st.map(x=>x.status))); fillSelect('source-status', unique(src.map(x=>x.verification_status || x.status)));
  ['leader-env','leader-round','leader-market','strategy-env','strategy-status','strategy-market','upcoming-env','upcoming-round','history-env','history-status','source-status'].forEach(id => { const el=document.getElementById(id); if(el && !el.dataset.bound) { el.addEventListener('change', renderAll); el.dataset.bound='1'; }});
  ['leader-search','strategy-search','history-search'].forEach(id => { const el=document.getElementById(id); if(el && !el.dataset.bound) { el.addEventListener('input', renderAll); el.dataset.bound='1'; }});
}
function selected(id) { return document.getElementById(id)?.value || 'ALL'; }
function matches(row, envId='leader-env', roundId='leader-round', marketId='leader-market', searchId='leader-search') {
  const env=selected(envId), round=selected(roundId), market=selected(marketId), search=(document.getElementById(searchId)?.value || '').toLowerCase();
  return (env==='ALL'||(row.env||row.environment)===env) && (round==='ALL'||row.round_code===round) && (market==='ALL'||row.market===market) && (!search||JSON.stringify(row).toLowerCase().includes(search));
}

function renderLeaderboard() {
  const rows=arr('leaderboard').filter(r=>matches(r));
  $('#leader-table tbody').innerHTML = rows.length ? rows.map(r => `<tr><td><button class="link-button strategy-link" data-id="${h(r.id || r.strategy_id)}">${h(r.id || r.strategy_id)}</button><small>${h(r.name || '')}</small></td><td><span class="pill env-${h(r.env)}">${h(r.env)}</span></td><td><span class="badge ${r.metric_status==='NO_DATA'?'neutral':'good'}">${h(r.metric_status || r.status || '—')}</span></td><td>${fmtN(r.total_bets)}</td><td>${fmtN(r.verified_bets)}</td><td>${r.metric_status==='NO_DATA'?'—':fmtPct(r.win_rate)}</td><td>${r.metric_status==='NO_DATA'?'—':fmtPct(r.verified_roi ?? r.roi)}</td><td>${r.metric_status==='NO_DATA'?'—':fmtMoney(r.verified_pnl ?? r.total_pnl)}</td><td><button class="text-button strategy-link" data-id="${h(r.id || r.strategy_id)}">Drill down</button></td></tr>`).join('') : `<tr><td colspan="9" class="empty-cell">No leaderboard rows match this filter.</td></tr>`;
  $$('.strategy-link').forEach(b=>b.addEventListener('click',()=>openStrategy(b.dataset.id)));
}

function roundStats(round) { return arr('leaderboard').filter(x=>x.env===round); }
function renderPostseason() {
  const m=summary(), rounds=['WC','DS','LCS','WS'];
  $('#postseason-banner').innerHTML = `<b>POST environment:</b> ${h((m.environment_breakdown?.POST?.status || 'NO DATA').replaceAll('_',' '))}. No postseason market price is invented. The transfer, adjusted, dedicated, round-specific and hierarchical experiments remain separate records.`;
  $('#round-cards').innerHTML = rounds.map(round => { const rows=roundStats(round); const evaluated=rows.reduce((n,r)=>n+(r.eval_picks||0),0); const verified=rows.reduce((n,r)=>n+(r.verified_bets||0),0); return `<article class="round-card"><div class="round-code">${h(round)}</div><h3>${h({WC:'Wild Card',DS:'Division Series',LCS:'League Championship Series',WS:'World Series'}[round])}</h3><div class="round-number">${fmtN(evaluated)} <small>evaluation picks</small></div><div class="round-detail">${fmtN(verified)} verified price wagers<br>${rows.length} strategy versions</div><button class="text-button" data-round-filter="${round}">Filter leaderboard →</button></article>`; }).join('');
  $$('[data-round-filter]').forEach(b=>b.addEventListener('click',()=>{go('leaderboard'); const e=document.getElementById('leader-round'); if(e){e.value=b.dataset.round; renderLeaderboard();}}));
  const exps=arr('research_experiments').filter(x=>String(x.id||'').startsWith('EXP_'));
  $('#model-comparison').innerHTML = exps.length ? `<div class="comparison-grid">${exps.map(x=>`<div class="comparison"><b>${h(x.id)}</b><span>${h(x.title)}</span><strong>${h(x.status || '—')}</strong><small>n=${fmtN(x.sample_size)} · Brier=${x.brier==null?'—':Number(x.brier).toFixed(4)} · verified ROI=${x.roi==null?'—':fmtPct(Number(x.roi)*100)}</small></div>`).join('')}</div>` : '<div class="empty">No A–E experiment has run on a source snapshot.</div>';
}

function renderStrategies() {
  const env=selected('strategy-env'), status=selected('strategy-status'), market=selected('strategy-market'), q=(document.getElementById('strategy-search')?.value||'').toLowerCase();
  const rows=arr('strategies').filter(s=>(env==='ALL'||s.env===env)&&(status==='ALL'||s.status===status)&&(market==='ALL'||s.market===market)&&(!q||JSON.stringify(s).toLowerCase().includes(q)));
  $('#strategy-cards').innerHTML=rows.length?rows.map(s=>`<article class="strategy-card"><div class="strategy-top"><span class="pill env-${h(s.env)}">${h(s.env)}</span><span class="badge ${s.status==='DATA_UNAVAILABLE'?'warning':'neutral'}">${h(s.status||'NOT_RUN')}</span></div><h3>${h(s.id)}</h3><h4>${h(s.name)}</h4><p>${h(s.hypothesis)}</p><dl><dt>Market</dt><dd>${h(s.market)}</dd><dt>Data gate</dt><dd>${h((s.data_requirements||[]).join(', ')||'—')}</dd><dt>Version</dt><dd>${h(s.version||'v1')}</dd></dl><button class="button secondary strategy-link" data-id="${h(s.id)}">Open strategy →</button></article>`).join(''):'<div class="empty">No strategy matches this filter.</div>';
  $$('.strategy-link').forEach(b=>b.addEventListener('click',()=>openStrategy(b.dataset.id)));
}

function renderUpcoming() {
  const rows=arr('upcoming_bets').filter(r=>(selected('upcoming-env')==='ALL'||(r.env||r.environment)===selected('upcoming-env'))&&(selected('upcoming-round')==='ALL'||r.round_code===selected('upcoming-round')));
  $('#upcoming-empty').style.display=rows.length?'none':'block'; $('#upcoming-empty').textContent=rows.length?'':'No upcoming predictions are published. A fresh forward-test run is required; no future game or price is guessed.';
  $('#upcoming-table tbody').innerHTML=rows.map(r=>`<tr><td>${h(r.decision_time)}</td><td>${h(r.strategy_version_id)}</td><td>${h(r.game_pk)}</td><td>${h(r.selection)}</td><td>${r.model_probability==null?'—':fmtPct(r.model_probability*100)}</td><td>${r.fair_price==null?'—':fmtPct(r.fair_price*100)}</td><td class="muted">${h(r.market_price ?? 'No observed quote')}</td><td><span class="badge warning">${h(r.status||'PROPOSED')}</span></td></tr>`).join('');
}
function renderPositions() {
  const rows=arr('open_positions'); $('#positions-empty').style.display=rows.length?'none':'block'; $('#positions-empty').textContent=rows.length?'':'No open paper positions. No fills or liquidity are simulated.';
  $('#positions-table tbody').innerHTML=rows.map(r=>`<tr><td>${h(r.position_id)}</td><td>${h(r.strategy_version_id)}</td><td>${h(r.opened_at)}</td><td>${h(r.state)}</td><td>${fmtN(r.quantity)}</td><td>${h(r.average_price)}</td><td>${h(r.source_observation_ids)}</td></tr>`).join('');
}
function renderHistory() {
  const q=(document.getElementById('history-search')?.value||'').toLowerCase(), env=selected('history-env'), stat=selected('history-status');
  const rows=arr('bets_ledger').filter(r=>(env==='ALL'||(r.env||r.environment)===env)&&(stat==='ALL'||(r.status||r.event_type)===stat)&&(!q||JSON.stringify(r).toLowerCase().includes(q)));
  $('#history-table tbody').innerHTML=rows.length?rows.map(r=>`<tr><td>${h(r.event_type||r.status||'BET')}</td><td>${h(r.bet_id)}</td><td>${h(r.game_pk)}</td><td>${h(r.strategy_id||r.strategy_version_id)}</td><td>${h(r.market)}</td><td>${h(r.selection)}</td><td>${h(r.market_price ?? r.price_american ?? '—')}</td><td>${h(r.result||'—')}</td><td>${r.pnl==null?'—':fmtMoney(r.pnl)}</td><td><span class="badge ${String(r.verification_status||'').includes('VERIFIED')?'good':'warning'}">${h(r.verification_status||'—')}</span></td></tr>`).join(''):'<tr><td colspan="10" class="empty-cell">No immutable wager records match this filter.</td></tr>';
  const dl=$('#download-ledger'); if(dl&&!dl.dataset.bound){dl.addEventListener('click',()=>{const blob=new Blob([JSON.stringify(arr('bets_ledger'),null,2)],{type:'application/json'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='mlbcomp-ledger.json'; a.click();}); dl.dataset.bound='1';}
}
function renderAnalytics() {
  const lb=arr('leaderboard'), evaluated=lb.filter(x=>x.metric_status!=='NO_DATA');
  const cards=[['Verified price wagers', evaluated.reduce((n,x)=>n+(x.verified_bets||0),0)],['Evaluation picks', evaluated.reduce((n,x)=>n+(x.eval_picks||0),0)],['Strategies with data', evaluated.length],['Open positions', summary().total_open_positions||0]];
  $('#analytics-cards').innerHTML=cards.map(c=>`<div class="metric"><span>${h(c[0])}</span><strong>${fmtN(c[1])}</strong><small>${evaluated.length?'from published data':'No source-backed performance is available'}</small></div>`).join('');
}
function renderResearch() {
  const rows=arr('research_experiments'); $('#research-list').innerHTML=rows.length?rows.map(r=>`<article class="research-card"><div><span class="eyebrow">${h(r.id||'QUESTION')}</span><h3>${h(r.title||r.hypothesis||'Research item')}</h3></div><span class="badge ${r.status==='DATA_UNAVAILABLE'||r.status==='NOT_RUN'?'warning':'neutral'}">${h(r.status||'—')}</span><p>${h(r.conclusion||'')}</p><div class="research-meta">n=${fmtN(r.sample_size)} · env=${h(r.env||'—')} · provenance=${h(r.provenance||'—')}</div></article>`).join(''):'<div class="empty">Research queue is empty. Register a source snapshot to run it.</div>';
}
function renderSources() {
  const status=selected('source-status'), rows=arr('registry').filter(r=>status==='ALL'||(r.verification_status||r.status)===status);
  $('#sources-table tbody').innerHTML=rows.length?rows.map(r=>`<tr><td><a href="${h(r.url)}" target="_blank" rel="noreferrer">${h(r.name)} ↗</a><small>${h(r.source_id||'')}</small></td><td>${h(r.data_type)}</td><td>${h(r.historical_depth)}</td><td>${h(r.current_availability)}</td><td>${h(r.access_method)}<br><small>${h(r.cost)}</small></td><td>${h(r.reliability)}</td><td><span class="badge ${r.verification_status==='VERIFIED'?'good':'warning'}">${h(r.verification_status)}</span>${r.verification_date?`<small>${h(r.verification_date)}</small>`:''}</td><td>${h(r.limitations)}</td></tr>`).join(''):'<tr><td colspan="8" class="empty-cell">No source registry rows are available.</td></tr>';
}
function renderVerification() {
  const rows=arr('audit_checks'), passed=rows.filter(r=>r.passed).length; $('#audit-summary').innerHTML=`<strong>${passed}/${rows.length||0}</strong><span>control checks pass</span><small>Controls are not a substitute for source availability.</small>`;
  $('#audit-list').innerHTML=rows.map(r=>`<div class="audit-item"><div><b>${h(r.name)}</b><span class="badge ${r.passed?'good':'bad'}">${r.passed?'PASS':'FAIL'}</span></div><small>${h(r.category)} · ${h(r.details)}</small></div>`).join('');
  $('#issues-list').innerHTML=arr('irregularities').map(r=>`<div class="issue"><div><b>${h(r.id)} · ${h(r.title)}</b><span class="badge ${r.severity==='CRITICAL'?'bad':'warning'}">${h(r.severity||r.status||'INFO')}</span></div><p>${h(r.description)}</p><small>Status: ${h(r.status||'—')} · Resolution: ${h(r.resolution||'open')}</small></div>`).join('');
}

function openStrategy(id) {
  const s=arr('strategies').find(x=>x.id===id||x.strategy_id===id); if(!s)return; const metrics=arr('leaderboard').filter(x=>x.id===id); const wagers=arr('bets_ledger').filter(x=>String(x.strategy_id||x.strategy_version_id||'').includes(id));
  $('#modal-body').innerHTML=`<p class="eyebrow">${h(s.env)} · ${h(s.version||'v1')}</p><h2>${h(s.id)}</h2><h3>${h(s.name)}</h3><span class="badge warning">${h(s.status||'NOT_RUN')}</span><div class="modal-grid"><div><b>Hypothesis</b><p>${h(s.hypothesis)}</p></div><div><b>Data requirements</b><p>${h((s.data_requirements||[]).join(', ')||'—')}</p></div><div><b>Entry rule</b><p>${h(s.entry_rule||'—')}</p></div><div><b>Required price</b><p>${h(s.required_price_rule||'—')}</p></div><div><b>Sizing</b><p>${h(s.sizing_rule||'—')}</p></div><div><b>Settlement</b><p>${h(s.settlement_rule||'—')}</p></div><div><b>Testing</b><p>${h(s.test_plan||'—')}</p></div><div><b>Limitations</b><p>${h(s.limitations||'—')}</p></div></div><hr><p><b>Published environment rows:</b> ${metrics.length} · <b>Individual ledger records:</b> ${wagers.length}</p><p class="muted">Performance is not shown as an edge claim when the data gate is not satisfied.</p>`;
  $('#strategy-modal').classList.add('open'); $('#strategy-modal').setAttribute('aria-hidden','false');
}
function closeModal(){ $('#strategy-modal').classList.remove('open'); $('#strategy-modal').setAttribute('aria-hidden','true'); }

document.addEventListener('DOMContentLoaded',()=>{initTabs(); $('#modal-close').addEventListener('click',closeModal); $('#strategy-modal').addEventListener('click',e=>{if(e.target.id==='strategy-modal')closeModal();}); loadData();});
