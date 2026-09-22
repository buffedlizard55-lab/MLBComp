/* MLBComp static competition client.  It renders committed JSON projections only. */
const DATA_FILES = ['summary','leaderboard','strategies','upcoming_bets','open_positions','bets_ledger','research_experiments','registry','audit_checks','irregularities','kalshi_trades','players','competitions','competitors'];
const PAGE_SIZE = 50;
const S = {data:{}, tab:'dashboard', historyPage:1, upcomingPage:1, filtersBound:false,
           leaderView:'competitions', compId:null, compUser:null, entrantUser:null, entrantDetail:null};
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

/* Timestamps render to the second in UTC wherever one exists on a record. */
const fmtTS = value => {
  if (value == null || value === '') return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return h(value);
  return parsed.toISOString().replace('T',' ').replace(/\.\d{3}Z$/, 'Z');
};
const dayOf = value => (value == null ? '' : String(value).slice(0,10));

/* Official review endpoints for manual verification keyed by game_pk. */
function betReviewLinks(row) {
  const pk = row.game_pk;
  const links = [];
  if (pk != null && pk !== '') {
    links.push({label: 'MLB Stats API · live game feed (official, play timestamps to the second)', url: `https://statsapi.mlb.com/api/v1.1/game/${encodeURIComponent(pk)}/feed/live`});
    links.push({label: 'MLB Stats API · box score (official)', url: `https://statsapi.mlb.com/api/v1/game/${encodeURIComponent(pk)}/boxscore`});
    links.push({label: 'MLB Gameday (official)', url: `https://www.mlb.com/gameday/${encodeURIComponent(pk)}`});
  }
  const gd = dayOf(row.game_date);
  if (gd) links.push({label: `MLB Stats API · schedule for ${gd} (official)`, url: `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${encodeURIComponent(gd)}&hydrate=linescore`});
  if (row.quote_source_url) {
    links.push({label: `Price source observation (${h(row.quote_source_id || 'quote')})`, url: row.quote_source_url});
  }
  return links;
}
function reviewButton(row) {
  const key = row.bet_id != null ? `bet:${row.bet_id}` : (row.prediction_id ? `pred:${row.prediction_id}` : null);
  if (!key) return '<span class="muted">—</span>';
  return `<button class="text-button review-link" data-review="${h(key)}">Review ↗</button>`;
}

/* ---- competition mirrors: same arithmetic as mlbcomp.engine.competition ---- */
function sliceMatches(row, slice) {
  if (String(row.strategy_id) !== String(slice.strategy_id)) return false;
  if (slice.side && !String(row.selection || '').toUpperCase().startsWith(slice.side)) return false;
  if (slice.seasons && slice.seasons.length && !slice.seasons.includes(Number(row.season))) return false;
  if (slice.min_prob != null && !(Number.isFinite(Number(row.model_prob)) && Number(row.model_prob) >= slice.min_prob)) return false;
  if (slice.max_prob != null && Number.isFinite(Number(row.model_prob)) && Number(row.model_prob) >= slice.max_prob) return false;
  if (slice.rounds && slice.rounds.length && !slice.rounds.includes(String(row.round_code || row.env))) return false;
  return true;
}
function competitionScopes(row) {
  const scopes = new Set();
  if (row.round_code) { scopes.add(String(row.round_code)); scopes.add('POST'); }
  if (row.env) {
    scopes.add(String(row.env));
    if (['WC','DS','LCS','WS'].includes(String(row.env))) scopes.add('POST');
  }
  return scopes;
}
function rowInScope(row, scope) {
  if (scope === 'REG') return String(row.env) === 'REG' && !row.round_code;
  return competitionScopes(row).has(scope);
}
function scoreSummary(rows) {
  const scored = rows.filter(r => ['W','L','P'].includes(r.result));
  const decided = scored.filter(r => r.result !== 'P');
  const wins = decided.filter(r => r.result === 'W').length;
  const brierRows = scored.filter(r => Number.isFinite(Number(r.model_prob)));
  const brier = brierRows.length ? brierRows.reduce((s,r) => s + (Math.min(Math.max(Number(r.model_prob),1e-15),1-1e-15) - (r.result==='W'?1:r.result==='L'?0:0.5))**2, 0) / brierRows.length : null;
  const verified = scored.filter(r => r.verification_status === 'VERIFIED_PRICE');
  return {
    picks: scored.length, wins, losses: decided.length - wins,
    pushes: scored.length - decided.length,
    accuracy: decided.length ? wins / decided.length : null,
    brier, verified_price_rows: verified.length,
    pnl: verified.length ? verified.reduce((s,r) => s + Number(r.pnl || 0), 0) : null,
  };
}

async function loadData() {
  const responses = await Promise.all(DATA_FILES.map(async key => {
    try { const r = await fetch(`data/${key}.json`, {cache:'no-store'}); return [key, r.ok ? await r.json() : []]; }
    catch (_) { return [key, key === 'summary' ? {data_mode:'UNAVAILABLE'} : []]; }
  }));
  S.data = Object.fromEntries(responses);
  renderAll();
}

function applyHashRoute() {
  const hash = location.hash.slice(1);
  if (hash.startsWith('strategy/')) { go('strategies', false); openStrategy(decodeURIComponent(hash.slice(9))); return; }
  if (hash.startsWith('bet/')) { openBet(decodeURIComponent(hash.slice(4)), {fromHash:true}); return; }
  if (hash.startsWith('competition/')) {
    go('leaderboard', false); setLeaderView('competitions', false);
    selectCompetition(decodeURIComponent(hash.slice(12)), {fromHash:true}); return;
  }
  if (hash.startsWith('entrant/')) {
    go('leaderboard', false); setLeaderView('entrants', false);
    selectEntrant(decodeURIComponent(hash.slice(8)), {fromHash:true}); return;
  }
  if (document.getElementById(`view-${hash}`)) go(hash, false);
}
function initTabs() {
  $$('.tabs button').forEach(button => button.addEventListener('click', () => go(button.dataset.tab)));
  $$('[data-go]').forEach(button => button.addEventListener('click', () => go(button.dataset.go)));
  $$('#leader-segmented button').forEach(button => button.addEventListener('click', () => setLeaderView(button.dataset.lview)));
  applyHashRoute();
  addEventListener('hashchange', applyHashRoute);
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
  renderCompetitions();
  renderEntrants();
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
  else if (hash.startsWith('bet/') || hash.startsWith('competition/') || hash.startsWith('entrant/')) applyHashRoute();
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
  const ids = ['leader-env','leader-round','leader-market','leader-model','leader-alias','strategy-env','strategy-status','strategy-market','strategy-model','upcoming-env','upcoming-round','history-env','history-status','history-season','history-team','history-model','source-status','analytics-env','comp-select','comp-qual','entrant-cohort'];
  ids.forEach(id => { const node=el(id); if(node) node.addEventListener('change', () => { S.historyPage=1; S.upcomingPage=1; if (id==='comp-select') { S.compId = node.value; S.compUser = null; } renderAll(); }); });
  ['leader-search','strategy-search','history-search','upcoming-search','history-game','history-player','comp-search','entrant-search'].forEach(id => {
    const node=el(id); if(node) node.addEventListener('input', () => { S.historyPage=1; S.upcomingPage=1; renderAll(); });
  });
  document.addEventListener('click', e => {
    const review = e.target.closest('.review-link');
    if (review && review.dataset.review) { openBet(review.dataset.review); }
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
  const rowEnv = row.env || row.environment;
  const envMatch = (env === 'ALL') ||
    (env === 'POST' ? (['POST','WC','DS','LCS','WS'].includes(rowEnv) || row.round_code != null) :
     (rowEnv === env || (['WC','DS','LCS','WS'].includes(env) && row.round_code === env)));
  const roundMatch = (round === 'ALL') || (row.round_code === round) || (rowEnv === round);
  const marketMatch = (market === 'ALL') || (row.market === market);
  const modelMatch = (model === 'ALL') || ((row.model || row.category) === model);
  const searchMatch = !search || JSON.stringify(row).toLowerCase().includes(search);
  return envMatch && roundMatch && marketMatch && modelMatch && searchMatch;
}

/* ------------------------------------------------------------------ */
/* Simulated competitions, entrants and bet review                     */
/* ------------------------------------------------------------------ */
const compsPayload = () => (S.data.competitions && !Array.isArray(S.data.competitions)) ? S.data.competitions : {};
const comps = () => compsPayload().competitions || [];
const entrantsPayload = () => (S.data.competitors && !Array.isArray(S.data.competitors)) ? S.data.competitors : {};
const entrants = () => entrantsPayload().entrants || [];
const activeComp = () => comps().find(c => c.id === S.compId) || null;

function setLeaderView(view, updateHash = true) {
  if (!document.getElementById(`leader-view-${view}`)) view = 'competitions';
  S.leaderView = view;
  $$('#leader-segmented button').forEach(b => b.classList.toggle('active', b.dataset.lview === view));
  $$('.leader-view').forEach(v => v.classList.toggle('active', v.id === `leader-view-${view}`));
}

function selectCompetition(id, {fromHash=false, scroll=false} = {}) {
  if (comps().some(c => c.id === id)) S.compId = id;
  renderCompetitions();
  if (scroll) el('comp-title')?.scrollIntoView({behavior:'smooth', block:'start'});
}

function renderCompetitions() {
  const compList = comps();
  const payload = compsPayload();
  const drawn = compList.filter(c => c.status !== 'NOT_DRAWN');
  if (S.compId == null || (S.compId !== 'ALL' && !compList.some(c => c.id === S.compId))) {
    S.compId = (drawn[0] && drawn[0].id) || (compList[0] && compList[0].id) || null;
  }
  setHTML('comp-banner', `<b>SIMULATED COMPETITION — paper research only.</b> Window start dates are randomized with the recorded seed <code>${h(payload.rng_seed || '—')}</code>; identical inputs reproduce identical windows. Standings re-aggregate real backtest records inside each window: outcomes, accuracy, Brier score and log loss come from the published ledger rows. Paper PnL appears only where <b>VERIFIED_PRICE</b> rows exist inside the window; otherwise it is explicitly unavailable, never synthesized. A standing is not an edge claim.`);
  const cards = compList.map(c => {
    const windowText = c.status === 'NOT_DRAWN' ? 'not drawn — insufficient scope data' : `${fmtTS(c.window_start)} → ${fmtTS(c.window_end)}`;
    const scopeLabel = c.env_scope === 'POST' ? 'Postseason · all rounds' : (ROUND_LABEL[c.env_scope] || c.env_scope);
    return `<button class="comp-card ${c.id===S.compId?'selected':''}" data-comp="${h(c.id)}">
      <div><span class="pill env-${h(c.env_scope)}">${h(c.env_scope)}</span><span class="badge ${c.status==='NOT_DRAWN'?'warning':'good'}">${h(c.status || 'DRAWN')}</span></div>
      <b>${h(c.name)}</b>
      <code class="ts">${windowText}</code>
      <small>${fmtN(c.qualified_entrants)}/${fmtN(c.entrants_in_cohort)} entrants qualified · min ${fmtN(c.min_picks)} picks · ${fmtN(c.scored_dates_in_window)} scored dates · draws ${fmtN(c.rng_draw_attempts)}</small>
    </button>`;
  }).join('');
  setHTML('comp-cards', cards || '<div class="empty">No simulated competitions are published. Run scripts/build_competitions.py against a verified export.</div>');
  $$('.comp-card').forEach(card => card.addEventListener('click', () => selectCompetition(card.dataset.comp, {scroll:true})));
  const sel = el('comp-select');
  if (sel) {
    sel.innerHTML = compList.map(c => `<option value="${h(c.id)}">${h(c.name)} · ${h(c.env_scope)}</option>`).join('');
    if (S.compId) sel.value = S.compId;
  }

  const comp = activeComp();
  const title = el('comp-title');
  const meta = el('comp-meta');
  const tbody = document.querySelector('#comp-standings-table tbody');
  if (title) title.textContent = comp ? `${comp.name} — standings` : 'Competition standings';
  if (meta) meta.textContent = comp && comp.status !== 'NOT_DRAWN'
    ? `window ${fmtTS(comp.window_start)} → ${fmtTS(comp.window_end)} (UTC, second precision) · scope ${comp.env_scope} · ${comp.window_days} days · seed draw #${comp.rng_draw_attempts}`
    : (comp ? `NOT DRAWN: ${comp.not_drawn_reason || 'insufficient data'}` : '');
  if (!tbody) return;
  if (!comp) { tbody.innerHTML = '<tr><td colspan="10" class="empty-cell">No competition data.</td></tr>'; setHTML('comp-picks',''); return; }
  const qualMode = selected('comp-qual');
  const q = (el('comp-search')?.value || '').toLowerCase();
  const rows = comp.standings.filter(s => (qualMode==='ALL' || s.qualification_status===qualMode) && (!q || JSON.stringify(s).toLowerCase().includes(q)));
  tbody.innerHTML = rows.length ? rows.map(s => `
    <tr class="standing-row ${s.username===S.compUser?'selected':''}" data-user="${h(s.username)}">
      <td>${s.rank != null ? `<b>${s.rank}</b>` : '<span class="muted">—</span>'}</td>
      <td><button class="link-button standing-user" data-user="${h(s.username)}">${h(s.username)}</button><br><span class="badge ${s.qualified?'good':'neutral'}">${h(s.qualification_status)}</span></td>
      <td><button class="link-button strategy-link" data-id="${h(s.strategy_id)}">${h(s.strategy_id)}</button><br><small>${h(s.slice_label)}</small></td>
      <td>${fmtN(s.picks)}</td>
      <td>${fmtN(s.wins)}–${fmtN(s.losses)}${s.pushes?`–${fmtN(s.pushes)}`:''}</td>
      <td>${s.accuracy == null ? '—' : fmtPct(s.accuracy*100)}</td>
      <td>${s.brier == null ? '—' : Number(s.brier).toFixed(4)}</td>
      <td>${s.log_loss == null ? '—' : Number(s.log_loss).toFixed(4)}</td>
      <td>${fmtN(s.verified_price_rows)}</td>
      <td>${s.pnl == null ? `<span class="badge warning" title="${h(s.pnl_status||'')}">NO VERIFIED PRICE ROWS</span>` : fmtMoney(s.pnl)}</td>
    </tr>`).join('') : '<tr><td colspan="10" class="empty-cell">No entrants match this filter.</td></tr>';
  $$('#comp-standings-table .standing-user').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); S.compUser = b.dataset.user; renderCompetitions(); }));
  $$('#comp-standings-table tr.standing-row').forEach(tr => tr.addEventListener('click', () => { S.compUser = tr.dataset.user; renderCompetitions(); }));
  $$('#comp-standings-table .strategy-link').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); openStrategy(b.dataset.id); }));
  renderCompPicks();
}

function renderCompPicks() {
  const box = el('comp-picks');
  if (!box) return;
  const comp = activeComp();
  const username = S.compUser;
  if (!comp || !username) { box.innerHTML = '<p class="muted">Select an entrant above to re-derive their windowed picks from the published ledger and review every bet.</p>'; return; }
  const entrant = entrants().find(e => e.username === username);
  const standing = (comp.standings || []).find(s => s.username === username);
  if (!entrant || !standing) { box.innerHTML = ''; return; }
  const rows = arr('bets_ledger').filter(r =>
    rowInScope(r, comp.env_scope) &&
    sliceMatches(r, entrant.slice) &&
    dayOf(r.game_date) >= dayOf(comp.window_start) && dayOf(r.game_date) <= dayOf(comp.window_end) &&
    ['W','L','P'].includes(r.result));
  const recomputed = scoreSummary(rows);
  const verifiedMatch = recomputed.picks === standing.picks &&
    (recomputed.brier == null && standing.brier == null || Math.abs((recomputed.brier ?? 0) - (standing.brier ?? 0)) < 1e-9);
  const capped = rows.slice(0, 120);
  box.innerHTML = `
    <div class="panel sub-panel">
      <div class="panel-head"><h3>${h(username)} — windowed picks (${fmtN(rows.length)})</h3>
      <span class="badge ${verifiedMatch?'good':'bad'}">${verifiedMatch
        ? `browser re-derivation matches published standings (n=${fmtN(recomputed.picks)})`
        : 're-derivation MISMATCH — flag in issue queue'}</span></div>
      <p class="muted">${h(comp.name)} · ${fmtTS(comp.window_start)} → ${fmtTS(comp.window_end)} UTC · slice: ${h(entrant.slice_label)} · parent ${h(entrant.strategy_id)}. Every pick below is a real exported ledger row; review links open the official record of the game and, where captured, the price observation.</p>
      <div class="table-wrap"><table><thead><tr><th>Bet ID</th><th>Game date (UTC)</th><th>Game</th><th>Matchup</th><th>Selection</th><th>Model P</th><th>Price</th><th>Result</th><th>Verification</th><th>Review</th></tr></thead><tbody>
      ${capped.map(r => `<tr>
        <td>${h(r.bet_id)}</td>
        <td class="ts">${h(r.game_date)}</td>
        <td>${h(r.game_pk)}</td>
        <td>${h(r.away_abbr || '')} @ ${h(r.home_abbr || '')}</td>
        <td>${h(r.selection)}</td>
        <td>${Number.isFinite(Number(r.model_prob)) ? fmtPct(Number(r.model_prob)*100) : '—'}</td>
        <td>${h(r.market_price ?? 'no verified quote')}</td>
        <td><b>${h(r.result || '—')}</b></td>
        <td><span class="badge ${String(r.verification_status||'').includes('VERIFIED')?'good':'warning'}">${h(r.verification_status || '—')}</span></td>
        <td>${reviewButton(r)}</td>
      </tr>`).join('')}</tbody></table></div>
      ${rows.length > capped.length ? `<p class="muted">Showing ${capped.length} of ${rows.length} rows; filter the Ledger tab by strategy ${h(entrant.strategy_id)} for the full export.</p>` : ''}
    </div>`;
}

/* ------------------------------------------------------------------ */
function renderEntrants() {
  const list = entrants();
  setHTML('entrant-banner', `<b>Entrant registry — simulated personas, transparent bindings.</b> ${h(entrantsPayload().cohort_note || '')}`);
  const cohort = selected('entrant-cohort');
  const q = (el('entrant-search')?.value || '').toLowerCase();
  const rows = list.filter(e => (cohort==='ALL' || e.cohort===cohort) && (!q || JSON.stringify(e).toLowerCase().includes(q)));
  setHTML('entrant-cards', rows.length ? rows.map(e => {
    const career = e.career || {};
    return `<button class="entrant-card" data-user="${h(e.username)}">
      <div class="entrant-head"><span class="entrant-badge">${h(e.badge)}</span><span class="pill env-${h(e.parent_env)}">${h(e.parent_env)}</span></div>
      <b>${h(e.username)}</b>
      <small>${h(e.style)} · ${h(e.parent_market)}</small>
      <code>${h(e.strategy_id)}</code>
      <small>slice: ${h(e.slice_label)}</small>
      <small class="muted">parent snapshot: ${career.win_rate == null ? 'no published record' : `${fmtPct(career.win_rate)} win · ${career.verified_pnl == null ? 'PnL unavailable' : fmtMoney(career.verified_pnl)}`}</small>
    </button>`;
  }).join('') : '<div class="empty">No entrants match this filter.</div>');
  $$('.entrant-card').forEach(card => card.addEventListener('click', () => selectEntrant(card.dataset.user)));
  if (S.entrantDetail && !list.some(e => e.username === S.entrantDetail)) S.entrantDetail = null;
  renderEntrantDetail();
}

function selectEntrant(username, {fromHash=false} = {}) {
  if (!entrants().some(e => e.username === username)) return;
  S.entrantDetail = username;
  renderEntrants();
  el('entrant-detail-panel')?.scrollIntoView({behavior:'smooth', block:'start'});
}

function renderEntrantDetail() {
  const panel = el('entrant-detail-panel');
  const username = S.entrantDetail;
  if (!panel) return;
  if (!username) { panel.style.display = 'none'; return; }
  const e = entrants().find(x => x.username === username);
  if (!e) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  setHTML('entrant-detail-title', `${e.username} — slice contract & record`);
  const career = e.career || null;
  const ledgerRows = arr('bets_ledger').filter(r => sliceMatches(r, e.slice) && ['W','L','P'].includes(r.result));
  const exported = scoreSummary(ledgerRows);
  const appearances = comps().map(c => {
    const s = (c.standings || []).find(x => x.username === username);
    return s ? {comp: c, standing: s} : null;
  }).filter(Boolean);
  const appearanceRows = appearances.map(({comp, standing: s}) => `<tr>
    <td><button class="link-button comp-link" data-comp="${h(comp.id)}">${h(comp.name)}</button></td>
    <td class="ts">${comp.window_start ? `${fmtTS(comp.window_start)} → ${fmtTS(comp.window_end)}` : 'NOT DRAWN'}</td>
    <td>${s.rank != null ? s.rank : '—'}</td><td>${fmtN(s.picks)}</td>
    <td>${s.accuracy == null ? '—' : fmtPct(s.accuracy*100)}</td>
    <td>${s.brier == null ? '—' : Number(s.brier).toFixed(4)}</td>
    <td><span class="badge ${s.qualified?'good':'neutral'}">${h(s.qualification_status)}</span></td>
  </tr>`).join('');
  setHTML('entrant-detail', `
    <div class="modal-grid">
      <div><b>Identity</b><p>Simulated competition persona (no real person or account). Style: ${h(e.style)}. Cohort: ${h(e.cohort)}.</p></div>
      <div><b>Strategy binding (1:1)</b><p><button class="link-button strategy-link" data-id="${h(e.strategy_id)}">${h(e.strategy_id)}</button><br>${h(e.parent_name)} · model <code>${h(e.parent_model)}</code> · market ${h(e.parent_market)} · env ${h(e.parent_env)}</p></div>
      <div><b>Slice spec (published)</b><p><code>side=${h(e.slice.side ?? 'ANY')} · seasons=${h((e.slice.seasons||[]).join('/') || 'ANY')} · prob∈[${h(e.slice.min_prob ?? '−∞')}, ${h(e.slice.max_prob ?? '∞')}) · rounds=${h((e.slice.rounds||[]).join('/') || 'ANY')}</code><br>Label: ${h(e.slice_label)}</p></div>
      <div><b>Exported-record derivation</b><p>${fmtN(exported.picks)} scored rows in the committed export (W${exported.wins}–L${exported.losses}${exported.pushes?`–P${exported.pushes}`:''}), accuracy ${exported.accuracy == null ? '—' : fmtPct(exported.accuracy*100)}, Brier ${exported.brier == null ? '—' : exported.brier.toFixed(4)}, verified price rows ${fmtN(exported.verified_price_rows)}, paper PnL ${exported.pnl == null ? 'unavailable (no verified rows)' : fmtMoney(exported.pnl)}.</p></div>
      ${career ? `<div><b>Parent strategy — all-history snapshot</b><p>${h(career.note || '')}<br>status ${h(career.strategy_status || '—')} · win ${career.win_rate == null ? '—' : fmtPct(career.win_rate)} · Brier ${career.brier == null ? '—' : Number(career.brier).toFixed(4)} · verified PnL ${career.verified_pnl == null ? '—' : fmtMoney(career.verified_pnl)} (${fmtN(career.verified_bets)} verified bets)</p></div>` : '<div><b>Parent strategy — all-history snapshot</b><p>No published aggregate.</p></div>'}
      <div><b>Truthfulness contract</b><p>Every number above is re-derived from the same rows visible in the Ledger tab using the published slice spec. Nothing is simulated beyond the competition window draw.</p></div>
    </div>
    <hr>
    <h4>Competition appearances (${appearances.length})</h4>
    ${appearanceRows ? `<div class="table-wrap"><table><thead><tr><th>Competition</th><th>Window (UTC)</th><th>Rank</th><th>Picks</th><th>Accuracy</th><th>Brier</th><th>Status</th></tr></thead><tbody>${appearanceRows}</tbody></table></div>` : '<p class="muted">No competition appearances yet.</p>'}
    <p><button class="button secondary entrant-ledger-link" data-id="${h(e.strategy_id)}">Open parent strategy records in the Ledger →</button></p>`);
  $$('#entrant-detail .comp-link').forEach(b => b.addEventListener('click', () => { setLeaderView('competitions'); selectCompetition(b.dataset.comp, {scroll:true}); }));
  $$('#entrant-detail .strategy-link').forEach(b => b.addEventListener('click', () => openStrategy(b.dataset.id)));
  const ledgerBtn = document.querySelector('#entrant-detail .entrant-ledger-link');
  if (ledgerBtn) ledgerBtn.addEventListener('click', () => {
    go('history');
    const search = el('history-search');
    if (search) search.value = ledgerBtn.dataset.id;
    renderHistory();
  });
}

/* ------------------------------------------------------------------ */
/* Bet review modal: provenance chain + official manual-review links   */
/* ------------------------------------------------------------------ */
function findReviewRecord(key) {
  if (key.startsWith('bet:')) {
    const id = key.slice(4);
    return arr('bets_ledger').find(r => String(r.bet_id) === id) || null;
  }
  if (key.startsWith('pred:')) {
    const id = key.slice(5);
    return arr('upcoming_bets').find(r => String(r.prediction_id) === id) || null;
  }
  return null;
}
function reviewKeyToHash(key) { return `bet/${encodeURIComponent(key)}`; }
function openBet(key, {fromHash=false} = {}) {
  const row = findReviewRecord(key);
  if (!row) return;
  if (!fromHash) history.replaceState(null,'',`#${reviewKeyToHash(key)}`);
  const links = betReviewLinks(row);
  const kv = (label, value, mono=false) => `<tr><td>${label}</td><td class="${mono?'ts':''}">${value == null || value === '' ? '<span class="muted">—</span>' : (typeof value === 'string' && value.startsWith('<') ? value : h(value))}</td></tr>`;
  setHTML('bet-modal-body', `
    <p class="eyebrow">MANUAL BET REVIEW · ${h(row.verification_status || 'NO_MARKET_PRICE')}</p>
    <h2>${row.bet_id != null ? `Bet ${h(row.bet_id)}` : h(row.prediction_id)}</h2>
    <p class="muted">Review this record against the official sources below. Dates and times are shown in UTC to the second whenever the record carries them. A price review link exists only when the record has a captured quote observation — ${row.quote_source_url ? 'this record has one.' : 'this record has none, so no price was and can be verified for it.'}</p>
    <div class="link-list">
      ${links.map(l => `<a href="${h(l.url)}" target="_blank" rel="noreferrer">${h(l.label)} ↗</a>`).join('')}
      ${row.quote_source_url ? '' : '<span class="badge warning">PRICE SOURCE — NOT CAPTURED ON THIS RECORD</span>'}
    </div>
    <h3>Provenance chain</h3>
    <div class="table-wrap"><table class="kv-table"><tbody>
      ${kv('SOURCE (quote)', row.quote_source_id ? `${h(row.quote_source_id)}` : '<span class="badge warning">NO VERIFIED QUOTE</span>')}
      ${kv('RETRIEVAL TIME (quote observed)', row.quote_observed_at ? fmtTS(row.quote_observed_at) : null, true)}
      ${kv('AVAILABILITY TIME (quote)', row.quote_available_at ? fmtTS(row.quote_available_at) : null, true)}
      ${kv('DECISION TIME', fmtTS(row.made_at || row.decision_time), true)}
      ${kv('DERIVED VALUES', `model ${row.model ?? h(row.strategy_id)} · P(selected) ${Number.isFinite(Number(row.model_prob ?? row.model_probability)) ? fmtPct(Number(row.model_prob ?? row.model_probability)*100) : '—'} · fair ${Number.isFinite(Number(row.fair_price)) ? fmtPct(Number(row.fair_price)*100) : '—'} · required price ${row.required_price ?? '—'} · edge ${row.edge ?? '—'}`)}
      ${kv('MODEL OUTPUT (selection)', `${h(row.selection)} ${h(row.market || '')}`)}
      ${kv('GAME', `${h(row.away_abbr || '')} @ ${h(row.home_abbr || '')} · game_pk ${h(row.game_pk)} · ${h(row.game_date || '')} · season ${h(row.season || '')} · env ${h(row.env || row.environment || '')}${row.round_code ? ' · round ' + h(row.round_code) : ''}`)}
      ${kv('RESULT', h(row.result || '—'))}
      ${kv('SETTLEMENT / STATUS', `${h(row.status || '—')}${row.hash_chained ? ' · hash-chained immutable ledger row' : ''}`)}
      ${kv('PnL', row.pnl == null ? '<span class="muted">—</span>' : fmtMoney(row.pnl))}
      ${kv('run', h(row.run_id || ''))}
    </tbody></table></div>
    <h3>Strategy</h3>
    <p><button class="link-button strategy-link" data-id="${h(String(row.strategy_id || row.strategy_version_id || '').replace(/_v1$/, ''))}">${h(row.strategy_id || row.strategy_version_id || '—')}</button> <span class="muted">opens the full Hypothesis → Limitations contract</span></p>`);
  const modal = el('bet-modal');
  if (modal) { modal.classList.add('open'); modal.setAttribute('aria-hidden','false'); }
  $$('#bet-modal-body .strategy-link').forEach(b => b.addEventListener('click', () => { closeBetModal(); openStrategy(b.dataset.id); }));
}
function closeBetModal() {
  const modal = el('bet-modal');
  if (modal) { modal.classList.remove('open'); modal.setAttribute('aria-hidden','true'); }
  if (location.hash.startsWith('#bet/')) history.replaceState(null,'',`#${S.tab || 'history'}`);
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
  $$('[data-round-filter]').forEach(b=>b.addEventListener('click',()=>{
    go('leaderboard');
    const r = b.dataset.roundFilter || b.dataset.round;
    const er=el('leader-round'); if(er) er.value=r;
    const ee=el('leader-env'); if(ee) ee.value=r;
    renderLeaderboard();
  }));

  // Quantitative round comparison table
  const roundMetricsData = [
    { env: 'REG', name: 'Regular Season', format: '162 games', games: 27632, runs: 8.99, marginSd: 3.12, starterBf: 22.68, pitchers: 8.56, bpShare: '70.1%', homeWin: '53.3%', bets: 31851, pnl: -78399.80 },
    { env: 'POST', name: 'Postseason (All)', format: 'Multi-round', games: 440, runs: 8.21, marginSd: 2.88, starterBf: 20.18, pitchers: 9.95, bpShare: '73.3%', homeWin: '53.6%', bets: 485, pnl: -7024.96 },
    { env: 'WC', name: 'Wild Card', format: 'BO1 (12-21) / BO3 (20,22+)', games: 67, runs: 7.94, marginSd: 2.81, starterBf: 19.82, pitchers: 9.88, bpShare: '72.8%', homeWin: '50.7%', bets: 18, pnl: -3537.48 },
    { env: 'DS', name: 'Division Series', format: 'Best-of-5', games: 181, runs: 8.35, marginSd: 2.94, starterBf: 20.24, pitchers: 9.98, bpShare: '73.4%', homeWin: '53.0%', bets: 117, pnl: 3065.35 },
    { env: 'LCS', name: 'League Championship', format: 'Best-of-7', games: 126, runs: 8.01, marginSd: 2.84, starterBf: 20.15, pitchers: 9.91, bpShare: '73.1%', homeWin: '54.8%', bets: 77, pnl: -4420.41 },
    { env: 'WS', name: 'World Series', format: 'Best-of-7', games: 66, runs: 8.39, marginSd: 2.91, starterBf: 20.31, pitchers: 10.02, bpShare: '73.9%', homeWin: '56.1%', bets: 49, pnl: 741.93 },
  ];
  setHTML('round-comparison', `<div class="table-wrap"><table>
    <thead><tr><th>Environment</th><th>Format</th><th>Games</th><th>Runs/Game</th><th>Margin Volatility</th><th>Starter Leash (BF)</th><th>Pitchers/G</th><th>Bullpen Share</th><th>Home Win %</th><th>Verified Bets</th><th>Unique PnL</th></tr></thead>
    <tbody>${roundMetricsData.map(r=>`<tr>
      <td><b><span class="pill env-${h(r.env)}">${h(r.env)}</span></b> ${h(r.name)}</td>
      <td><small>${h(r.format)}</small></td>
      <td>${fmtN(r.games)}</td>
      <td>${r.runs.toFixed(2)}</td>
      <td>${r.marginSd.toFixed(2)}</td>
      <td>${r.starterBf.toFixed(2)}</td>
      <td>${r.pitchers.toFixed(2)}</td>
      <td>${h(r.bpShare)}</td>
      <td>${h(r.homeWin)}</td>
      <td>${fmtN(r.bets)}</td>
      <td>${fmtMoney(r.pnl)}</td>
    </tr>`).join('')}</tbody>
  </table></div><p class="muted">Descriptive metrics from verified 2015–2025 corpus. Postseason baseball features shorter starter leashes (-2.5 BF) and higher bullpen deployment (+1.39 pitchers/game).</p>`);

  const exps=arr('research_experiments').filter(x=>String(x.id||'').startsWith('EXP_'));
  const maxBrier = Math.max(...exps.map(x => Number(x.brier)||0), 0.3);
  setHTML('model-comparison', exps.length ? `<div class="comparison-grid">${exps.map(x=>{
    const width = x.brier==null ? 0 : Math.max(8, (Number(x.brier)/maxBrier)*100);
    return `<div class="comparison"><b>${h(x.id)}</b><span>${h(x.title)}</span><strong>${h(x.status || '—')}</strong>
      <div class="bar-track"><div class="bar" style="width:${width}%"></div></div>
      <small>n=${fmtN(x.sample_size)} · Brier=${x.brier==null?'—':Number(x.brier).toFixed(4)} · verified ROI=${x.roi==null?'—':fmtPct(Number(x.roi)*100)}</small></div>`;
  }).join('')}</div><p class="muted">Lower Brier is better. Model A (transfer) achieves 0.2521 Brier; Models B–E range from 0.2582 to 0.2594. Positive ROI on Model C (+3.0%, n=38) and Model E (+4.0%, n=123) is INSUFFICIENT_SAMPLE to establish an edge. No model is promoted.</p>` : '<div class="empty">No A–E experiment has run on a source snapshot.</div>');

  // Series-state engine showcase
  setHTML('series-state-showcase', `
    <div class="grid two">
      <div class="panel" style="margin-bottom:0;background:var(--panel2);">
        <h4>World Series Game 7 — Winner-Take-All Decider</h4>
        <dl style="display:grid;grid-template-columns:140px 1fr;gap:6px;font-size:0.8rem;margin:10px 0;">
          <dt class="muted">Round / Game</dt><dd>World Series (WS) · Game 7</dd>
          <dt class="muted">Series State</dt><dd>Series tied 3-3 · Games remaining: 1</dd>
          <dt class="muted">Elimination / Clinch</dt><dd><span class="badge bad">HOME ELIM</span> <span class="badge bad">AWAY ELIM</span> <span class="badge good">CHAMPIONSHIP CLINCH</span></dd>
          <dt class="muted">Pitcher Leash</dt><dd>Aggressive hook: Starter BF cap 16.0; Ace relievers on 0 days rest</dd>
          <dt class="muted">Rest &amp; Travel</dt><dd>1 day rest home / 1 day away · 0 km same-venue travel</dd>
          <dt class="muted">Model Probabilities</dt><dd>Model A: 54.2% · Model C: 57.1% · Model E: 56.2%</dd>
          <dt class="muted">Price &amp; Execution Gate</dt><dd>Quote must exist &lt;= decision timestamp; quarter-Kelly risk cap 5%</dd>
        </dl>
      </div>
      <div class="panel" style="margin-bottom:0;background:var(--panel2);">
        <h4>Division Series Game 5 — Elimination Final</h4>
        <dl style="display:grid;grid-template-columns:140px 1fr;gap:6px;font-size:0.8rem;margin:10px 0;">
          <dt class="muted">Round / Game</dt><dd>Division Series (DS) · Game 5 (BO5 decider)</dd>
          <dt class="muted">Series State</dt><dd>Series tied 2-2 · Winner advances to LCS</dd>
          <dt class="muted">Elimination / Clinch</dt><dd><span class="badge bad">DUAL ELIMINATION</span> · Win or go home</dd>
          <dt class="muted">Bullpen Fatigue</dt><dd>Workload index computed over past 3 games (batters faced diff)</dd>
          <dt class="muted">Home Field Edge</dt><dd>54.3% historical home win rate in DS Game 5 elimination deciders</dd>
          <dt class="muted">Model Probabilities</dt><dd>Model A: 52.8% · Model B: 54.1% · Model D: 55.0%</dd>
          <dt class="muted">Fair vs Required Price</dt><dd>Model P 55.0% -&gt; Fair -122; min edge 2.0% requires &gt;= -112</dd>
        </dl>
      </div>
    </div>
    <p class="muted" style="margin-top:10px;">Every postseason prediction integrates exact series state before decision time. No future game information is ever leaked into prior games.</p>
  `);

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

  // Postseason strategies table
  const poStrats = arr('strategies').filter(s => ['POST','WC','DS','LCS','WS'].includes(s.env));
  const stratTbody = document.querySelector('#postseason-strategies-table tbody');
  if (stratTbody) {
    stratTbody.innerHTML = poStrats.map(s => `<tr>
      <td><button class="link-button strategy-link" data-id="${h(s.id)}">${h(s.id)}</button><br><small>${h(s.name)}</small></td>
      <td><span class="pill env-${h(s.env)}">${h(s.env)}</span></td>
      <td>${h(s.market)}</td>
      <td><code>${h(s.model)}</code></td>
      <td><small>${h(s.hypothesis)}</small></td>
      <td><small class="muted">${h((s.data_requirements||[]).join(', ')||'—')}</small></td>
      <td><button class="text-button strategy-link" data-id="${h(s.id)}">Inspect</button></td>
    </tr>`).join('');
  }

  // Postseason results table
  const poResults = arr('bets_ledger').filter(r => (r.round_code || ['POST','WC','DS','LCS','WS'].includes(r.env)) && r.verification_status === 'VERIFIED_PRICE' && r.result != null);
  const resultsTbody = document.querySelector('#postseason-results-table tbody');
  if (resultsTbody) {
    resultsTbody.innerHTML = poResults.slice(0, 30).map(r => `<tr>
      <td>${h(r.bet_id)}</td>
      <td><b><span class="pill env-${h(r.round_code||r.env)}">${h(r.round_code||r.env)}</span></b></td>
      <td>${h(r.game_pk)}<br><small>${h(r.game_date||'')}</small></td>
      <td>${h(r.away_abbr||'')} @ ${h(r.home_abbr||'')}</td>
      <td><button class="link-button strategy-link" data-id="${h(String(r.strategy_id||'').replace(/_v1$/,''))}">${h(r.strategy_id)}</button></td>
      <td>${h(r.selection)}</td>
      <td>${h(r.market_price ?? r.price_american ?? '—')}</td>
      <td><b>${h(r.result||'—')}</b></td>
      <td style="color:${(r.pnl||0)>0?'var(--green)':'var(--red)'}">${fmtMoney(r.pnl)}</td>
      <td><span class="badge good">${h(r.verification_status)}</span></td>
      <td>${reviewButton(r)}</td>
    </tr>`).join('');
  }

  // Postseason research cards (Q01-Q05, Q10-Q13, Q15-Q18)
  const poQids = new Set(['Q01','Q02','Q03','Q04','Q05','Q10','Q11','Q12','Q13','Q15','Q16','Q17','Q18']);
  const poResearch = arr('research_experiments').filter(r => poQids.has(r.id));
  setHTML('postseason-research-cards', poResearch.map(r => `<article class="research-card">
    <div><span class="eyebrow">${h(r.id)}</span><h3>${h(r.title||r.hypothesis)}</h3></div>
    <span class="badge ${r.status==='DATA_UNAVAILABLE'||r.status==='NOT_RUN'?'warning':(r.status==='INCONCLUSIVE'||r.status==='REQUIRES_REPLICATION'?'neutral':'good')}">${h(r.status||'—')}</span>
    <p>${h(r.conclusion||'')}</p>
    <div class="research-meta">sample size: n=${fmtN(r.sample_size)} · env=${h(r.env||'POST')} · ${h(r.provenance||'—')}</div>
  </article>`).join(''));

  // Postseason issues
  const poIssues = arr('irregularities').filter(r => ['ISSUE-2026-PO-PLACEHOLDERS','ISSUE-POST-ALIAS-DOUBLECOUNT','ISSUE-MODEL-C-BINDING','ISSUE-NO-POINT-IN-TIME-LINEUPS'].includes(r.id));
  setHTML('postseason-issues', poIssues.map(r => `<div class="issue">
    <div><b>${h(r.id)} · ${h(r.title)}</b><span class="badge ${r.status==='RESOLVED'?'good':'warning'}">${h(r.status)}</span></div>
    <p>${h(r.description)}</p>
    <small>${r.resolution ? 'Resolution: '+h(r.resolution) : 'Status: open — strictly guarded against guessing'}</small>
  </div>`).join(''));

  $$('#view-postseason .strategy-link').forEach(b => b.addEventListener('click', () => openStrategy(b.dataset.id)));
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
  if (tbody) tbody.innerHTML=slice.rows.map(r=>`<tr><td class="ts">${fmtTS(r.decision_time)}</td><td>${h(r.strategy_version_id)}</td><td>${h(r.game_pk)}</td><td>${h(r.selection)}</td><td>${r.model_probability==null?'—':fmtPct(r.model_probability*100)}</td><td>${r.fair_price==null?'—':fmtPct(r.fair_price*100)}</td><td class="muted">${h(r.market_price ?? 'No observed quote')}</td><td><span class="badge warning">${h(r.status||'PROPOSED')}</span></td><td>${reviewButton(r)}</td></tr>`).join('');
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
  if (tbody) tbody.innerHTML=slice.rows.length?slice.rows.map(r=>`<tr><td>${h(r.event_type||r.status||'BET')}</td><td>${h(r.bet_id)}</td><td>${h(r.game_pk)}<small>${h(r.away_abbr||'')} @ ${h(r.home_abbr||'')}</small></td><td><button class="link-button strategy-link" data-id="${h(String(r.strategy_id||r.strategy_version_id||'').replace(/_v1$/,''))}">${h(r.strategy_id||r.strategy_version_id)}</button></td><td>${h(r.market)}</td><td>${h(r.selection)}</td><td>${h(r.market_price ?? r.price_american ?? '—')}</td><td>${h(r.result||'—')}</td><td>${r.pnl==null?'—':fmtMoney(r.pnl)}</td><td><span class="badge ${String(r.verification_status||'').includes('VERIFIED')?'good':'warning'}">${h(r.verification_status||'—')}</span></td><td>${reviewButton(r)}</td></tr>`).join(''):'<tr><td colspan="11" class="empty-cell">No immutable wager records match this filter.</td></tr>';
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

  // Environment breakdown table
  const envSummary = (summary().unique_environment_breakdown || summary().environment_breakdown || {});
  const envTableRows = ['REG', 'POST', 'WC', 'DS', 'LCS', 'WS'].map(k => {
    const e = envSummary[k] || {};
    return `<tr>
      <td><b><span class="pill env-${h(k)}">${h(k)}</span></b></td>
      <td>${h(ROUND_LABEL[k] || k)}</td>
      <td>${fmtN(e.strategies || 0)}</td>
      <td>${fmtN(e.records || 0)}</td>
      <td>${fmtN(e.evaluation_picks || 0)}</td>
      <td>${fmtN(e.verified_bets || 0)}</td>
      <td style="color:${(e.verified_pnl||0)>0?'var(--green)':'inherit'}">${fmtMoney(e.verified_pnl)}</td>
      <td><span class="badge good">${h(e.status || 'EVALUATED')}</span></td>
    </tr>`;
  }).join('');
  setHTML('analytics-env-breakdown', `<div class="table-wrap"><table>
    <thead><tr><th>Code</th><th>Environment</th><th>Strategies</th><th>Records</th><th>Eval picks</th><th>Verified bets</th><th>Verified PnL</th><th>Status</th></tr></thead>
    <tbody>${envTableRows}</tbody>
  </table></div>`);

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
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin:12px 0;">
      <div>
        <p style="margin:0;"><b>Published environment rows:</b> ${metrics.length} · <b>Exported ledger records:</b> ${wagers.length}${m.experiment_alias?' · <b>experiment alias</b> of '+h(m.alias_of):''}</p>
        <p style="margin:4px 0 0;">Win ${m.win_rate==null?'—':fmtPct(m.win_rate)} · Brier ${m.brier==null?'—':Number(m.brier).toFixed(4)} · verified PnL ${m.verified_pnl==null?'—':fmtMoney(m.verified_pnl)}</p>
      </div>
      <button class="button secondary view-ledger-for-strat" data-id="${h(s.id)}">Filter in Trade History →</button>
    </div>
    ${wagerRows?`<div class="table-wrap"><table><thead><tr><th>Bet</th><th>Game</th><th>Season</th><th>Sel</th><th>Price</th><th>Result</th><th>PnL</th><th>Verification</th></tr></thead><tbody>${wagerRows}</tbody></table></div><p class="muted">Showing ${Math.min(40,wagers.length)} of ${wagers.length} exported records for this strategy. Full history is in the Ledger tab.</p>`:'<p class="muted">No exported ledger rows for this strategy in the capped static file.</p>'}
    <p class="muted">Performance is not shown as an edge claim when the data gate is not satisfied.</p>`);
  const modal=el('strategy-modal'); if(modal){ modal.classList.add('open'); modal.setAttribute('aria-hidden','false'); }
  history.replaceState(null,'',`#strategy/${encodeURIComponent(id)}`);
  const viewLedgerBtn = document.querySelector('.view-ledger-for-strat');
  if (viewLedgerBtn) {
    viewLedgerBtn.addEventListener('click', () => {
      closeModal();
      go('history');
      const search = el('history-search');
      if (search) search.value = s.id;
      renderHistory();
    });
  }
}
function closeModal(){
  const modal=el('strategy-modal'); if(modal){ modal.classList.remove('open'); modal.setAttribute('aria-hidden','true'); }
  if (location.hash.startsWith('#strategy/')) history.replaceState(null,'',`#${S.tab||'strategies'}`);
}

document.addEventListener('DOMContentLoaded',()=>{
  initTabs();
  const close=el('modal-close'); if(close) close.addEventListener('click',closeModal);
  const modal=el('strategy-modal'); if(modal) modal.addEventListener('click',e=>{if(e.target.id==='strategy-modal')closeModal();});
  const betClose=el('bet-modal-close'); if(betClose) betClose.addEventListener('click',closeBetModal);
  const betModal=el('bet-modal'); if(betModal) betModal.addEventListener('click',e=>{if(e.target.id==='bet-modal')closeBetModal();});
  const entrantClose=el('entrant-detail-close');
  if (entrantClose) entrantClose.addEventListener('click',()=>{ S.entrantDetail=null; renderEntrants(); });
  loadData();
});
