/* MLBComp static competition client.  It renders committed JSON projections only. */
const DATA_FILES = ['summary','leaderboard','strategies','upcoming_bets','open_positions','bets_ledger','research_experiments','registry','audit_checks','irregularities','kalshi_trades','players'];
const PAGE_SIZE = 50;
const S = {data:{}, tab:'dashboard', historyPage:1, upcomingPage:1, filtersBound:false};
const $ = (q, root=document) => (typeof q === 'string' && q.startsWith('#') ? (root.getElementById ? root.getElementById(q.slice(1)) : root.querySelector(q)) : root.querySelector(q));
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const el = id => document.getElementById(id);
const setHTML = (id, html) => { const node = el(id); if (node) node.innerHTML = html; return node; };
const h = value => String(value ?? '—').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const arr = key => Array.isArray(S.data[key]) ? S.data[key] : [];
const summary = () => S.data.summary || {};
const fmtN = v => v == null || v === '' ? '—' : Number(v).toLocaleString(undefined,{maximumFractionDigits:2});
const fmtPct = v => v == null || v === '' ? '—' : `${Number(v).toFixed(2)}%`;
const fmtMoney = v => v == null || v === '' ? '—' : `${Number(v) < 0 ? '−' : ''}$${Math.abs(Number(v)).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;
const unique = values => [...new Set(values.filter(v => v != null && v !== ''))].sort();
const ROUND_LABEL = {WC:'Wild Card',DS:'Division Series',LCS:'League Championship Series',WS:'World Series',POST:'Postseason',REG:'Regular season'};

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
  if (hash.startsWith('strategy/')) {
    go('strategies', false);
  } else if (document.getElementById(`view-${hash}`)) {
    go(hash, false);
  }
  addEventListener('hashchange', () => {
    const tab = location.hash.slice(1);
    if (tab.startsWith('strategy/')) { go('strategies', false); openStrategy(decodeURIComponent(tab.slice(9))); return; }
    if (document.getElementById(`view-${tab}`)) go(tab, false);
  });
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
  const modeEl = el('data-mode');
  if (modeEl) {
    modeEl.textContent = mode.replaceAll('_',' ');
    modeEl.className = `status ${mode === 'SOURCE_SNAPSHOT' ? 'verified' : 'warning'}`;
  }
  setHTML('truth-banner', `<b>${h(mode.replaceAll('_',' '))}.</b> ${mode === 'SOURCE_SNAPSHOT' ? 'Only source-backed observations are displayed.' : 'No source snapshot is loaded. Strategy hypotheses and audit controls are visible; no game, price, fill, result or PnL is asserted.'} <span>Every output is paper-only.</span>`);
  const heroMode = el('hero-mode'); if (heroMode) heroMode.textContent = mode.replaceAll('_',' ');
  const heroAsof = el('hero-asof'); if (heroAsof) heroAsof.textContent = `as of ${h(m.as_of_date || '—')}`;
  const footer = el('footer-meta'); if (footer) footer.textContent = `${fmtN(m.total_strategies)} strategy versions · ${fmtN(m.total_simulated_bets)} wager records · ${h(m.as_of_date || '')}`;
  bindFilters();
  renderDashboard();
  renderLeaderboard();
  renderPostseason();
  renderStrategies();
  renderUpcoming();
  renderPositions();
  renderHistory();
  renderAnalytics();
  renderResearch();
  renderSources();
  renderVerification();
  const hash = location.hash.slice(1);
  if (hash.startsWith('strategy/')) openStrategy(decodeURIComponent(hash.slice(9)));
}

function renderDashboard() {
  const m = summary();
  const uniquePnl = m.unique_verified_pnl != null ? m.unique_verified_pnl : m.total_simulated_pnl;
  const cards = [
    ['Games tracked', fmtN(m.total_games_tracked), `${fmtN(m.completed_games)} completed · ${fmtN(m.upcoming_games)} upcoming`],
    ['Strategy library', fmtN(m.total_strategies), 'versioned hypotheses'],
    ['Ledger records', fmtN(m.total_simulated_bets), 'append-only projection'],
    ['Unique verified PnL', uniquePnl == null ? 'No data' : fmtMoney(uniquePnl), 'aliases excluded'],
    ['Open paper positions', fmtN(m.total_open_positions), 'no real orders'],
    ['Source mode', (m.data_mode || '—').replaceAll('_',' '), h(m.current_stage || '')],
  ];
  setHTML('kpis', cards.map(c => `<div class="kpi"><span>${h(c[0])}</span><strong>${c[1]}</strong><small>${c[2]}</small></div>`).join(''));
  const note = el('alias-note');
  if (note) note.textContent = m.alias_note || '';
  const audits = arr('audit_checks'); const passed = audits.filter(x => x.passed).length;
  setHTML('health', `<div class="health-row"><strong>${passed}/${audits.length || 0}</strong><span>control checks passing</span></div><div class="health-row"><strong>${fmtN(arr('registry').length)}</strong><span>sources in registry</span></div><div class="health-row"><strong>${fmtN(arr('irregularities').length)}</strong><span>issue records</span></div><p class="muted">A passing control does not mean a data source is available.</p>`);
  const uniqueEnv = m.unique_environment_breakdown || m.environment_breakdown || {};
  const rawEnv = m.environment_breakdown || {};
  setHTML('environment-summary', ['REG','POST','WC','DS','LCS','WS'].map(k => {
    const e = uniqueEnv[k] || {};
    const raw = rawEnv[k] || {};
    const extra = k === 'POST' && raw.verified_bets && raw.verified_bets !== e.verified_bets
      ? `<small>raw incl. aliases ${fmtN(raw.verified_bets)}</small>` : `<small>${h(e.status || 'NO DATA')}</small>`;
    return `<div class="env-row"><span><b>${h(k)}</b> ${h(ROUND_LABEL[k] || k)}</span><strong>${fmtN(e.verified_bets || 0)} verified</strong>${extra}</div>`;
  }).join(''));
  setHTML('season-status', `
    <div class="notice-grid">
      <div><span class="dot amber"></span><b>Regular season 2026</b><p>Snapshot as-of ${h(m.as_of_date || '—')}. ${fmtN(m.upcoming_games)} scheduled games remain without a final score. Stats API was unreachable on 2026-09-22 (TLS closed); no live standings are asserted.</p></div>
      <div><span class="dot purple"></span><b>Postseason 2026</b><p>Bracket not loaded. Placeholder Wild Card / DS / LCS / WS team names are excluded from games. No postseason wager is proposed for an undetermined matchup.</p></div>
      <div><span class="dot green"></span><b>Paper only</b><p>Kalshi, exchanges and sportsbooks are not connected. ${fmtN(m.total_kalshi_trades)} Kalshi trades recorded.</p></div>
    </div>`);
}

function fillSelect(id, values, allLabel='All') {
  const select = el(id); if (!select) return;
  const current = select.value || 'ALL';
  const keepFirst = select.dataset.keepFirst === '1';
  if (!keepFirst) {
    select.innerHTML = '';
    const option = document.createElement('option'); option.value='ALL'; option.textContent=allLabel; select.appendChild(option);
  } else {
    const first = select.options[0];
    select.innerHTML = '';
    if (first) select.appendChild(first);
  }
  values.forEach(v => {
    if ([...select.options].some(o => o.value === String(v))) return;
    const o=document.createElement('option'); o.value=v; o.textContent=v; select.appendChild(o);
  });
  select.value = [...select.options].some(o=>o.value===current) ? current : (select.options[0]?.value || 'ALL');
}
function bindFilters() {
  if (S.filtersBound) return;
  const ids = ['leader-env','leader-round','leader-market','leader-model','leader-alias','strategy-env','strategy-status','strategy-market','strategy-model','upcoming-env','upcoming-round','history-env','history-status','history-season','history-team','history-model','source-status','analytics-env'];
  ids.forEach(id => { const node=el(id); if(node) node.addEventListener('change', () => { S.historyPage=1; S.upcomingPage=1; renderAll(); }); });
  ['leader-search','strategy-search','history-search','upcoming-search','history-game','history-player'].forEach(id => {
    const node=el(id); if(node) node.addEventListener('input', () => { S.historyPage=1; S.upcomingPage=1; renderAll(); });
  });
  S.filtersBound = true;
}
function populateFilters() {
  const lb=arr('leaderboard'), st=arr('strategies'), up=arr('upcoming_bets'), ledger=arr('bets_ledger'), src=arr('registry');
  ['leader-env','strategy-env','upcoming-env','history-env','analytics-env'].forEach(id => fillSelect(id, unique([...lb,...st,...up,...ledger].map(x=>x.env || x.environment))));
  fillSelect('leader-market', unique(lb.map(x=>x.market)));
  fillSelect('strategy-market', unique(st.map(x=>x.market)));
  fillSelect('upcoming-round', unique(up.map(x=>x.round_code)));
  fillSelect('history-status', unique(ledger.map(x=>x.status || x.event_type)));
  fillSelect('strategy-status', unique(st.map(x=>x.status)));
  fillSelect('source-status', unique(src.map(x=>x.verification_status || x.status)));
  fillSelect('leader-model', unique(lb.map(x=>x.model || x.category)));
  fillSelect('strategy-model', unique(st.map(x=>x.model)));
  fillSelect('history-season', unique(ledger.map(x=>x.season)));
  fillSelect('history-model', unique(ledger.map(x=>x.model)));
  fillSelect('history-team', unique(ledger.flatMap(x=>[x.home_abbr, x.away_abbr]).filter(Boolean)));
}
function selected(id) { return el(id)?.value || 'ALL'; }
function matches(row, envId='leader-env', roundId='leader-round', marketId='leader-market', searchId='leader-search', modelId='leader-model') {
  const env=selected(envId), round=selected(roundId), market=selected(marketId), model=modelId?selected(modelId):'ALL', search=(el(searchId)?.value || '').toLowerCase();
  return (env==='ALL'||(row.env||row.environment)===env) && (round==='ALL'||row.round_code===round) && (market==='ALL'||row.market===market) && (model==='ALL'||(row.model||row.category)===model) && (!search||JSON.stringify(row).toLowerCase().includes(search));
}

function renderLeaderboard() {
  populateFilters();
  const aliasMode = selected('leader-alias');
  const rows=arr('leaderboard').filter(r => {
    if (!matches(r)) return false;
    if (aliasMode==='UNIQUE' && r.experiment_alias) return false;
    if (aliasMode==='ALIASES' && !r.experiment_alias) return false;
    return true;
  });
  const body = rows.length ? rows.map(r => `<tr>
    <td><button class="link-button strategy-link" data-id="${h(r.id || r.strategy_id)}">${h(r.id || r.strategy_id)}</button><small>${h(r.name || '')}${r.experiment_alias?' · alias':''}</small></td>
    <td><span class="pill env-${h(r.env)}">${h(r.env)}</span></td>
    <td><span class="badge ${r.metric_status==='NO_DATA'?'neutral':'good'}">${h(r.metric_status || r.status || '—')}</span></td>
    <td>${fmtN(r.total_bets)}</td><td>${fmtN(r.verified_bets)}</td>
    <td>${r.metric_status==='NO_DATA'?'—':fmtPct(r.win_rate)}</td>
    <td>${r.brier==null?'—':Number(r.brier).toFixed(4)}</td>
    <td>${r.metric_status==='NO_DATA'?'—':fmtPct(r.verified_roi ?? r.roi)}</td>
    <td>${r.metric_status==='NO_DATA'?'—':fmtMoney(r.verified_pnl ?? r.total_pnl)}</td>
    <td><button class="text-button strategy-link" data-id="${h(r.id || r.strategy_id)}">Drill down</button></td>
  </tr>`).join('') : `<tr><td colspan="10" class="empty-cell">No leaderboard rows match this filter.</td></tr>`;
  const tbody = document.querySelector('#leader-table tbody');
  if (tbody) tbody.innerHTML = body;
  $$('.strategy-link').forEach(b=>b.addEventListener('click',()=>openStrategy(b.dataset.id)));
}

function roundStats(round) { return arr('leaderboard').filter(x=>x.env===round && !x.experiment_alias); }
function renderPostseason() {
  const m=summary(), rounds=['WC','DS','LCS','WS'];
  setHTML('postseason-banner', `<b>POST environment:</b> ${h(((m.unique_environment_breakdown||m.environment_breakdown)?.POST?.status || 'NO DATA').replaceAll('_',' '))}. Unique verified POST bets exclude experiment aliases. No postseason market price is invented. The transfer, adjusted, dedicated, round-specific and hierarchical experiments remain separate records.`);
  setHTML('round-cards', rounds.map(round => {
    const rows=roundStats(round);
    const evaluated=rows.reduce((n,r)=>n+(r.eval_picks||0),0);
    const verified=rows.reduce((n,r)=>n+(r.verified_bets||0),0);
    const pnl=rows.reduce((n,r)=>n+(r.verified_pnl||0),0);
    return `<article class="round-card"><div class="round-code">${h(round)}</div><h3>${h(ROUND_LABEL[round])}</h3><div class="round-number">${fmtN(evaluated)} <small>evaluation picks</small></div><div class="round-detail">${fmtN(verified)} verified price wagers<br>${fmtMoney(verified?pnl:null)} unique PnL<br>${rows.length} strategy versions</div><button class="text-button" data-round-filter="${round}">Filter leaderboard →</button></article>`;
  }).join(''));
  $$('[data-round-filter]').forEach(b=>b.addEventListener('click',()=>{go('leaderboard'); const e=el('leader-round'); if(e){e.value=b.dataset.roundFilter || b.dataset.round; renderLeaderboard();}}));
  const exps=arr('research_experiments').filter(x=>String(x.id||'').startsWith('EXP_'));
  const maxBrier = Math.max(...exps.map(x => Number(x.brier)||0), 0.3);
  setHTML('model-comparison', exps.length ? `<div class="comparison-grid">${exps.map(x=>{
    const width = x.brier==null ? 0 : Math.max(8, (Number(x.brier)/maxBrier)*100);
    return `<div class="comparison"><b>${h(x.id)}</b><span>${h(x.title)}</span><strong>${h(x.status || '—')}</strong>
      <div class="bar-track"><div class="bar" style="width:${width}%"></div></div>
      <small>n=${fmtN(x.sample_size)} · Brier=${x.brier==null?'—':Number(x.brier).toFixed(4)} · verified ROI=${x.roi==null?'—':fmtPct(Number(x.roi)*100)}</small></div>`;
  }).join('')}</div><p class="muted">Lower Brier is better. No model is promoted. EXP_C is series-state (the evaluated dedicated model in this snapshot); MLB_POST_DEDICATED_001 (postseason Elo) is catalogued and not yet run.</p>` : '<div class="empty">No A–E experiment has run on a source snapshot.</div>');
  const poUp=arr('upcoming_bets').filter(x=>x.round_code);
  const names=Object.fromEntries(arr('players').map(p=>[p.player_id,p.name]));
  if(!poUp.length){
    setHTML('postseason-upcoming','<div class="empty">No upcoming postseason predictions. The 2026 bracket rows use placeholder team names until clinched — no wager is asserted for an undetermined matchup.</div>');
  } else {
    setHTML('postseason-upcoming',`<div class="table-wrap"><table><thead><tr><th>Decision</th><th>Round / game</th><th>Strategy</th><th>Series state</th><th>Probable pitchers</th><th>Model P</th><th>Fair</th><th>Required</th><th>Price</th><th>Edge</th></tr></thead><tbody>${poUp.map(r=>{
      const ss=r.series_state, pp=r.probable_pitchers;
      const ssTxt=ss?`G${ss.game_number??"?"} · ${ss.wins_a_before??0}-${ss.wins_b_before??0}${ss.elimination_a?' · ELIM A':''}${ss.elimination_b?' · ELIM B':''}${ss.clinch_a?' · CLINCH A':''}${ss.clinch_b?' · CLINCH B':''} · rest ${ss.days_rest_home??'—'}/${ss.days_rest_away??'—'}`:'no series-state row';
      const ppTxt=pp?`${names[pp.home]||pp.home||'—'} vs ${names[pp.away]||pp.away||'—'}`:`<span class="muted">${h(r.probable_pitchers_status||'DATA_UNAVAILABLE')}</span>`;
      return `<tr><td>${h(r.decision_time)}</td><td><b>${h(r.round_code)}</b> ${h(r.game_pk)}<br><small>${h(r.game_date||'')}</small></td><td>${h(r.strategy_version_id)}</td><td><small>${ssTxt}</small></td><td><small>${ppTxt}</small></td><td>${r.model_probability==null?'—':fmtPct(r.model_probability*100)}</td><td>${r.fair_price==null?'—':fmtPct(r.fair_price*100)}</td><td>${r.required_price==null?'—':h(r.required_price)}</td><td class="muted">no observed quote</td><td class="muted">—</td></tr>`;
    }).join('')}</tbody></table></div><p class="muted">Every row is PROPOSED: no verified price exists at the decision timestamp, so no stake, fill or PnL is asserted.</p>`);
  }
}

function renderStrategies() {
  populateFilters();
  const env=selected('strategy-env'), status=selected('strategy-status'), market=selected('strategy-market'), model=selected('strategy-model'), q=(el('strategy-search')?.value||'').toLowerCase();
  const rows=arr('strategies').filter(s=>(env==='ALL'||s.env===env)&&(status==='ALL'||s.status===status)&&(market==='ALL'||s.market===market)&&(model==='ALL'||s.model===model)&&(!q||JSON.stringify(s).toLowerCase().includes(q)));
  setHTML('strategy-cards', rows.length?rows.map(s=>`<article class="strategy-card"><div class="strategy-top"><span class="pill env-${h(s.env)}">${h(s.env)}</span><span class="badge ${s.status==='DATA_UNAVAILABLE'||s.status==='NOT_RUN'?'warning':'neutral'}">${h(s.status||'NOT_RUN')}</span></div><h3>${h(s.id)}</h3><h4>${h(s.name)}</h4><p>${h(s.hypothesis)}</p><dl><dt>Market</dt><dd>${h(s.market)}</dd><dt>Data gate</dt><dd>${h((s.data_requirements||[]).join(', ')||'—')}</dd><dt>Version</dt><dd>${h(s.version||'v1')}</dd></dl><button class="button secondary strategy-link" data-id="${h(s.id)}">Open strategy →</button></article>`).join(''):'<div class="empty">No strategy matches this filter.</div>');
  $$('#strategy-cards .strategy-link').forEach(b=>b.addEventListener('click',()=>openStrategy(b.dataset.id)));
}

function pageSlice(rows, page) {
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const p = Math.min(Math.max(1, page), pages);
  return {rows: rows.slice((p-1)*PAGE_SIZE, p*PAGE_SIZE), page:p, pages, total: rows.length};
}
function pagerHTML(id, page, pages, total) {
  if (!total) return '';
  return `<span>${fmtN(total)} rows · page ${page}/${pages}</span>
    <button class="button secondary" data-pager="${id}" data-dir="-1" ${page<=1?'disabled':''}>Prev</button>
    <button class="button secondary" data-pager="${id}" data-dir="1" ${page>=pages?'disabled':''}>Next</button>`;
}
function bindPagers() {
  if (S.pagersBound) return;
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-pager]');
    if (!b) return;
    const dir = Number(b.dataset.dir);
    if (b.dataset.pager === 'history') S.historyPage += dir;
    if (b.dataset.pager === 'upcoming') S.upcomingPage += dir;
    renderAll();
  });
  S.pagersBound = true;
}

function renderUpcoming() {
  const q=(el('upcoming-search')?.value||'').toLowerCase();
  const all=arr('upcoming_bets').filter(r=>(selected('upcoming-env')==='ALL'||(r.env||r.environment)===selected('upcoming-env'))&&(selected('upcoming-round')==='ALL'||r.round_code===selected('upcoming-round'))&&(!q||JSON.stringify(r).toLowerCase().includes(q)));
  const empty = el('upcoming-empty');
  if (empty) { empty.style.display=all.length?'none':'block'; empty.textContent=all.length?'':'No upcoming predictions are published. A fresh forward-test run is required; no future game or price is guessed.'; }
  const slice = pageSlice(all, S.upcomingPage); S.upcomingPage = slice.page;
  const tbody = document.querySelector('#upcoming-table tbody');
  if (tbody) tbody.innerHTML=slice.rows.map(r=>`<tr><td>${h(r.decision_time)}</td><td>${h(r.strategy_version_id)}</td><td>${h(r.game_pk)}</td><td>${h(r.selection)}</td><td>${r.model_probability==null?'—':fmtPct(r.model_probability*100)}</td><td>${r.fair_price==null?'—':fmtPct(r.fair_price*100)}</td><td class="muted">${h(r.market_price ?? 'No observed quote')}</td><td><span class="badge warning">${h(r.status||'PROPOSED')}</span></td></tr>`).join('');
  setHTML('upcoming-pager', pagerHTML('upcoming', slice.page, slice.pages, slice.total));
  bindPagers();
}
function renderPositions() {
  const rows=arr('open_positions');
  const empty=el('positions-empty');
  if (empty) { empty.style.display=rows.length?'none':'block'; empty.textContent=rows.length?'':'No open paper positions. No fills or liquidity are simulated.'; }
  const tbody=document.querySelector('#positions-table tbody');
  if (tbody) tbody.innerHTML=rows.map(r=>`<tr><td>${h(r.position_id)}</td><td>${h(r.strategy_version_id)}</td><td>${h(r.opened_at)}</td><td>${h(r.state)}</td><td>${fmtN(r.quantity)}</td><td>${h(r.average_price)}</td><td>${h(r.source_observation_ids)}</td></tr>`).join('');
}
function renderHistory() {
  const q=(el('history-search')?.value||'').toLowerCase(), env=selected('history-env'), stat=selected('history-status'),
        season=selected('history-season'), team=selected('history-team'), model=selected('history-model'),
        game=(el('history-game')?.value||'').trim(), player=(el('history-player')?.value||'').trim();
  const all=arr('bets_ledger').filter(r=>
    (env==='ALL'||(r.env||r.environment)===env)&&
    (stat==='ALL'||(r.status||r.event_type)===stat)&&
    (season==='ALL'||String(r.season)===season)&&
    (team==='ALL'||r.home_abbr===team||r.away_abbr===team||String(r.home_team_id)===team||String(r.away_team_id)===team)&&
    (model==='ALL'||r.model===model)&&
    (!game||String(r.game_pk)===game)&&
    (!player||JSON.stringify(r).includes(player))&&
    (!q||JSON.stringify(r).toLowerCase().includes(q)));
  const slice = pageSlice(all, S.historyPage); S.historyPage = slice.page;
  setHTML('history-count', all.length ? `${fmtN(all.length)} matching records (exported ledger is capped).` : '');
  const tbody=document.querySelector('#history-table tbody');
  if (tbody) tbody.innerHTML=slice.rows.length?slice.rows.map(r=>`<tr><td>${h(r.event_type||r.status||'BET')}</td><td>${h(r.bet_id)}</td><td>${h(r.game_pk)}<small>${h(r.away_abbr||'')} @ ${h(r.home_abbr||'')}</small></td><td><button class="link-button strategy-link" data-id="${h(String(r.strategy_id||r.strategy_version_id||'').replace(/_v1$/,''))}">${h(r.strategy_id||r.strategy_version_id)}</button></td><td>${h(r.market)}</td><td>${h(r.selection)}</td><td>${h(r.market_price ?? r.price_american ?? '—')}</td><td>${h(r.result||'—')}</td><td>${r.pnl==null?'—':fmtMoney(r.pnl)}</td><td><span class="badge ${String(r.verification_status||'').includes('VERIFIED')?'good':'warning'}">${h(r.verification_status||'—')}</span></td></tr>`).join(''):'<tr><td colspan="10" class="empty-cell">No immutable wager records match this filter.</td></tr>';
  setHTML('history-pager', pagerHTML('history', slice.page, slice.pages, slice.total));
  bindPagers();
  $$('#history-table .strategy-link').forEach(b=>b.addEventListener('click',()=>openStrategy(b.dataset.id)));
  const dl=el('download-ledger'); if(dl&&!dl.dataset.bound){dl.addEventListener('click',()=>{const blob=new Blob([JSON.stringify(arr('bets_ledger'),null,2)],{type:'application/json'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='mlbcomp-ledger.json'; a.click();}); dl.dataset.bound='1';}
}
function renderAnalytics() {
  const envSel=selected('analytics-env');
  const lb=arr('leaderboard').filter(x=>(envSel==='ALL'||x.env===envSel) && !x.experiment_alias);
  const evaluated=lb.filter(x=>x.metric_status!=='NO_DATA');
  const calibr=lb.filter(x=>x.brier!=null);
  const avgBrier=calibr.length?calibr.reduce((s,x)=>s+Number(x.brier),0)/calibr.length:null;
  const uniquePnl = evaluated.reduce((n,x)=>n+(x.verified_pnl||0),0);
  const cards=[['Verified price wagers', evaluated.reduce((n,x)=>n+(x.verified_bets||0),0)],['Evaluation picks', evaluated.reduce((n,x)=>n+(x.eval_picks||0),0)],['Strategies with data', evaluated.length],['Calibrated strategies', calibr.length],['Mean Brier (calibrated)', avgBrier==null?null:Number(avgBrier).toFixed(4)],['Unique verified PnL', evaluated.some(x=>x.verified_pnl!=null)?uniquePnl:null]];
  setHTML('analytics-cards', cards.map(c=>`<div class="metric"><span>${h(c[0])}</span><strong>${c[1]==null?'No data':(String(c[0]).includes('PnL')?fmtMoney(c[1]):fmtN(c[1]))}</strong><small>${evaluated.length?'from published data, aliases excluded':'No source-backed performance is available'}</small></div>`).join(''));
  const clvRows=arr('bets_ledger').filter(r=>r.clv!=null && (envSel==='ALL'||r.env===envSel));
  const clvNote=clvRows.length?`${clvRows.length} verified rows carry closing-line value (mean CLV ${(clvRows.reduce((s,r)=>s+Number(r.clv),0)/clvRows.length).toFixed(4)}).`:'No verified closing-line value yet — CLV requires a two-tier verified quote.';
  const calTable=calibr.slice().sort((a,b)=>Number(a.brier)-Number(b.brier)).slice(0,16).map(x=>`<tr><td>${h(x.id)}</td><td><span class="pill env-${h(x.env)}">${h(x.env)}</span></td><td>${Number(x.brier).toFixed(4)}</td><td>${x.log_loss==null?'—':Number(x.log_loss).toFixed(4)}</td><td>${fmtN(x.calibration_n)}</td><td><div class="bar-track"><div class="bar" style="width:${Math.min(100, Number(x.brier)*200)}%"></div></div></td></tr>`).join('');
  setHTML('analytics-detail', `<p class="muted">${h(clvNote)}</p>`+(calTable?`<div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Env</th><th>Brier</th><th>Log loss</th><th>n</th><th></th></tr></thead><tbody>${calTable}</tbody></table></div>`:'<p class="muted">No calibration rows published yet.</p>'));
}
function renderResearch() {
  const rows=arr('research_experiments');
  setHTML('research-list', rows.length?rows.map(r=>`<article class="research-card"><div><span class="eyebrow">${h(r.id||'QUESTION')}</span><h3>${h(r.title||r.hypothesis||'Research item')}</h3></div><span class="badge ${r.status==='DATA_UNAVAILABLE'||r.status==='NOT_RUN'?'warning':(r.status==='INCONCLUSIVE'||r.status==='REQUIRES_REPLICATION'?'neutral':'good')}">${h(r.status||'—')}</span><p>${h(r.conclusion||'')}</p><div class="research-meta">n=${fmtN(r.sample_size)} · env=${h(r.env||'—')} · provenance=${h(r.provenance||'—')}</div></article>`).join(''):'<div class="empty">Research queue is empty. Register a source snapshot to run it.</div>');
}
function renderSources() {
  const status=selected('source-status'), rows=arr('registry').filter(r=>status==='ALL'||(r.verification_status||r.status)===status);
  const tbody=document.querySelector('#sources-table tbody');
  if (tbody) tbody.innerHTML=rows.length?rows.map(r=>`<tr><td><a href="${h(r.url)}" target="_blank" rel="noreferrer">${h(r.name)} ↗</a><small>${h(r.source_id||'')}</small></td><td>${h(r.data_type)}</td><td>${h(r.historical_depth)}</td><td>${h(r.current_availability)}</td><td>${h(r.access_method)}<br><small>${h(r.cost)}</small></td><td>${h(r.reliability)}</td><td><span class="badge ${r.verification_status==='VERIFIED'?'good':'warning'}">${h(r.verification_status)}</span>${r.verification_date?`<small>${h(r.verification_date)}</small>`:''}</td><td>${h(r.limitations)}</td></tr>`).join(''):'<tr><td colspan="8" class="empty-cell">No source registry rows are available.</td></tr>';
}
function renderVerification() {
  const rows=arr('audit_checks'), passed=rows.filter(r=>r.passed).length;
  setHTML('audit-summary', `<strong>${passed}/${rows.length||0}</strong><span>control checks pass</span><small>Controls are not a substitute for source availability.</small>`);
  setHTML('audit-list', rows.map(r=>`<div class="audit-item"><div><b>${h(r.name)}</b><span class="badge ${r.passed?'good':'bad'}">${r.passed?'PASS':'FAIL'}</span></div><small>${h(r.category)} · ${h(r.details)}</small></div>`).join(''));
  setHTML('issues-list', arr('irregularities').map(r=>`<div class="issue"><div><b>${h(r.id)} · ${h(r.title)}</b><span class="badge ${r.severity==='CRITICAL'||r.severity==='HIGH'?'bad':'warning'}">${h(r.severity||r.status||'INFO')}</span></div><p>${h(r.description)}</p><small>Status: ${h(r.status||'—')} · Resolution: ${h(r.resolution||'open')}</small></div>`).join(''));
}

function openStrategy(id) {
  const s=arr('strategies').find(x=>x.id===id||x.strategy_id===id); if(!s)return;
  const metrics=arr('leaderboard').filter(x=>x.id===id);
  const wagers=arr('bets_ledger').filter(x=>String(x.strategy_id||x.strategy_version_id||'').includes(id));
  const m = metrics[0] || {};
  const wagerRows = wagers.slice(0, 40).map(r => `<tr><td>${h(r.bet_id)}</td><td>${h(r.game_pk)}</td><td>${h(r.season)}</td><td>${h(r.selection)}</td><td>${h(r.market_price ?? '—')}</td><td>${h(r.result||'—')}</td><td>${r.pnl==null?'—':fmtMoney(r.pnl)}</td><td>${h(r.verification_status||'—')}</td></tr>`).join('');
  setHTML('modal-body', `<p class="eyebrow">${h(s.env)} · ${h(s.version||'v1')}</p><h2>${h(s.id)}</h2><h3>${h(s.name)}</h3><span class="badge warning">${h(s.status||'NOT_RUN')}</span>
    <div class="modal-grid">
      <div><b>Hypothesis</b><p>${h(s.hypothesis)}</p></div>
      <div><b>Data requirements</b><p>${h((s.data_requirements||[]).join(', ')||'—')}</p></div>
      <div><b>Entry rule</b><p>${h(s.entry_rule||'—')}</p></div>
      <div><b>Required price</b><p>${h(s.required_price_rule||'—')}</p></div>
      <div><b>Sizing</b><p>${h(s.sizing_rule||'—')}</p></div>
      <div><b>Settlement</b><p>${h(s.settlement_rule||'—')}</p></div>
      <div><b>Testing</b><p>${h(s.test_plan||'—')}</p></div>
      <div><b>Limitations</b><p>${h(s.limitations||'—')}</p></div>
    </div>
    <hr>
    <p><b>Published environment rows:</b> ${metrics.length} · <b>Exported ledger records:</b> ${wagers.length}${m.experiment_alias?' · <b>experiment alias</b> of '+h(m.alias_of):''}</p>
    <p>Win ${m.win_rate==null?'—':fmtPct(m.win_rate)} · Brier ${m.brier==null?'—':Number(m.brier).toFixed(4)} · verified PnL ${m.verified_pnl==null?'—':fmtMoney(m.verified_pnl)}</p>
    ${wagerRows?`<div class="table-wrap"><table><thead><tr><th>Bet</th><th>Game</th><th>Season</th><th>Sel</th><th>Price</th><th>Result</th><th>PnL</th><th>Verification</th></tr></thead><tbody>${wagerRows}</tbody></table></div><p class="muted">Showing ${Math.min(40,wagers.length)} of ${wagers.length} exported records for this strategy. Full history is in the Ledger tab.</p>`:'<p class="muted">No exported ledger rows for this strategy in the capped static file.</p>'}
    <p class="muted">Performance is not shown as an edge claim when the data gate is not satisfied.</p>`);
  const modal=el('strategy-modal'); if(modal){ modal.classList.add('open'); modal.setAttribute('aria-hidden','false'); }
  history.replaceState(null,'',`#strategy/${encodeURIComponent(id)}`);
}
function closeModal(){
  const modal=el('strategy-modal'); if(modal){ modal.classList.remove('open'); modal.setAttribute('aria-hidden','true'); }
  if (location.hash.startsWith('#strategy/')) history.replaceState(null,'',`#${S.tab||'strategies'}`);
}

document.addEventListener('DOMContentLoaded',()=>{
  initTabs();
  const close=el('modal-close'); if(close) close.addEventListener('click',closeModal);
  const modal=el('strategy-modal'); if(modal) modal.addEventListener('click',e=>{if(e.target.id==='strategy-modal')closeModal();});
  loadData();
});
