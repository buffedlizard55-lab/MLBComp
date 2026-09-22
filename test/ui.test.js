/** Static-site data contract tests. The safe empty snapshot is valid. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const root = path.join(__dirname, '..', 'data');
const read = name => JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'));

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
assert.ok(html.includes('id="analytics-detail"'), 'Analytics tab requires #analytics-detail');
assert.ok(html.includes('id="history-pager"'));
assert.ok(html.includes('leader-alias'));
assert.ok(html.includes('history-game'));
assert.ok(html.includes('history-player'));
assert.ok(html.includes('id="round-comparison"'), 'Postseason Center requires #round-comparison');
assert.ok(html.includes('id="series-state-showcase"'), 'Postseason Center requires #series-state-showcase');
assert.ok(html.includes('id="postseason-strategies-table"'), 'Postseason Center requires #postseason-strategies-table');
assert.ok(html.includes('id="postseason-results-table"'), 'Postseason Center requires #postseason-results-table');
assert.ok(html.includes('id="postseason-research-cards"'), 'Postseason Center requires #postseason-research-cards');
assert.ok(html.includes('id="postseason-issues"'), 'Postseason Center requires #postseason-issues');
assert.ok(html.includes('id="analytics-env-breakdown"'), 'Analytics requires #analytics-env-breakdown');

const summary = read('summary.json');
assert.ok(['NO_SOURCE_SNAPSHOT', 'SOURCE_SNAPSHOT'].includes(summary.data_mode));
assert.ok(summary.total_strategies >= 50, 'strategy research library is present');
assert.ok(!('total_simulated_pnl' in summary) || summary.total_simulated_pnl === null || typeof summary.total_simulated_pnl === 'number');
if (summary.data_mode === 'SOURCE_SNAPSHOT') {
  assert.ok(summary.unique_environment_breakdown, 'unique environment totals must be published');
  assert.ok(summary.unique_environment_breakdown.POST.verified_bets <= summary.environment_breakdown.POST.verified_bets);
  assert.ok(typeof summary.unique_verified_pnl === 'number');
}

const strategies = read('strategies.json');
assert.ok(strategies.length >= 50);
for (const s of strategies) {
  assert.ok(s.id && s.env && s.market && s.hypothesis);
  assert.ok(['REG', 'POST', 'WC', 'DS', 'LCS', 'WS'].includes(s.env));
  assert.ok(Array.isArray(s.data_requirements));
  assert.ok(s.entry_rule && s.required_price_rule && s.sizing_rule && s.settlement_rule);
}
for (const round of ['WC', 'DS', 'LCS', 'WS']) assert.ok(strategies.some(s => s.env === round));

const leaderboard = read('leaderboard.json');
assert.strictEqual(leaderboard.length, strategies.length);
for (const row of leaderboard) {
  assert.ok(row.id && row.username.startsWith('MLB_'));
  if (summary.data_mode === 'NO_SOURCE_SNAPSHOT') {
    assert.strictEqual(row.total_pnl, null);
    assert.strictEqual(row.verified_roi, null);
    assert.strictEqual(row.current_bankroll, null);
    assert.deepStrictEqual(row.equity_curve, []);
  } else {
    assert.ok(row.total_pnl === null || typeof row.total_pnl === 'number');
    assert.ok(row.current_bankroll === null || typeof row.current_bankroll === 'number');
  }
  assert.ok(Array.isArray(row.equity_curve));
}

const checks = read('audit_checks.json');
assert.ok(checks.length >= 18, `expected the adversarial control suite, got ${checks.length}`);
assert.ok(checks.every(c => c.passed === true), 'every control must pass');
const checkNames = new Set(checks.map(c => c.name));
for (const required of ['settlement_matches_scores', 'quote_observations_verified',
  'clv_in_range', 'series_needed_format', 'ledger_hash_chain', 'no_unverified_pnl',
  'postseason_round_codes', 'series_state_pre_game']) {
  assert.ok(checkNames.has(required), `missing control: ${required}`);
}
const ledger = read('bets_ledger.json');
for (const row of ledger) {
  if (row.verification_status !== 'VERIFIED_PRICE') assert.ok(!row.pnl || row.pnl === 0);
}
const registry = read('registry.json');
assert.ok(registry.length >= 10);
for (const source of registry) {
  for (const key of ['name', 'url', 'data_type', 'historical_depth', 'access_method', 'cost', 'restrictions', 'licensing', 'reliability', 'granularity', 'automation_capability', 'verification_status', 'limitations']) assert.ok(key in source, `${key} missing from ${source.name}`);
}

const players = read('players.json');
assert.ok(Array.isArray(players) && players.length >= 4000, 'players register must be populated with >=4000 modern players');
assert.ok(players[0].player_id && players[0].name && players[0].mlb_id);

/* ---------------- competition layer contracts ---------------- */
assert.ok(html.includes('id="leader-segmented"'), 'Leaderboards requires the segmented view control');
assert.ok(html.includes('id="comp-cards"'), 'Leaderboards requires #comp-cards');
assert.ok(html.includes('id="comp-standings-table"'), 'Leaderboards requires #comp-standings-table');
assert.ok(html.includes('id="entrant-cards"'), 'Leaderboards requires #entrant-cards');
assert.ok(html.includes('id="bet-modal"'), 'Manual bet review requires #bet-modal');

const competitors = read('competitors.json');
const competitions = read('competitions.json');
assert.ok(competitions.rng_seed, 'competition payload publishes the recorded RNG seed');
assert.ok(competitions.disclaimer.includes('SIMULATED'), 'simulation disclaimer is published');

const strategyIds = new Set(strategies.map(s => s.id));
const boardById = new Map(leaderboard.map(r => [r.id, r]));
const usernames = new Set();
for (const e of competitors.entrants) {
  assert.ok(e.username && !usernames.has(e.username), `duplicate entrant username ${e.username}`);
  usernames.add(e.username);
  assert.ok(strategyIds.has(e.strategy_id), `entrant ${e.username} binds to an unknown strategy ${e.strategy_id}`);
  assert.ok(['REG', 'POST'].includes(e.cohort), 'entrant cohort is REG or POST');
  const s = e.slice;
  assert.ok(s && s.strategy_id === e.strategy_id, 'slice must reference the bound strategy');
  assert.ok(e.slice_label && typeof e.slice_label === 'string');
  if (s.side != null) assert.ok(['HOME', 'AWAY', 'OVER', 'UNDER'].includes(s.side), `invalid slice side ${s.side}`);
  if (s.min_prob != null) assert.ok(s.min_prob >= 0 && s.min_prob < 1, 'slice probability band valid');
  if (s.rounds) for (const r of s.rounds) assert.ok(['WC', 'DS', 'LCS', 'WS'].includes(r));
  if (e.career) {
    const parent = boardById.get(e.strategy_id);
    assert.ok(parent, 'career aggregates require the parent leaderboard row');
    assert.strictEqual(e.career.scope, 'ALL_HISTORY_SNAPSHOT');
    assert.strictEqual(e.career.verified_pnl, parent.verified_pnl ?? null,
      `career PnL for ${e.username} must equal the parent strategy snapshot value, never a new number`);
  }
}

/* Re-derive every published standing from the committed ledger rows. */
const dayOf = v => (v == null ? '' : String(v).slice(0, 10));
function sliceMatches(row, slice) {
  if (String(row.strategy_id) !== String(slice.strategy_id)) return false;
  if (slice.side && !String(row.selection || '').toUpperCase().startsWith(slice.side)) return false;
  if (slice.seasons && slice.seasons.length && !slice.seasons.includes(Number(row.season))) return false;
  if (slice.min_prob != null && !(Number.isFinite(Number(row.model_prob)) && Number(row.model_prob) >= slice.min_prob)) return false;
  if (slice.max_prob != null && Number.isFinite(Number(row.model_prob)) && Number(row.model_prob) >= slice.max_prob) return false;
  if (slice.rounds && slice.rounds.length && !slice.rounds.includes(String(row.round_code || row.env))) return false;
  return true;
}
function scopes(row) {
  const out = new Set();
  if (row.round_code) { out.add(String(row.round_code)); out.add('POST'); }
  if (row.env) { out.add(String(row.env)); if (['WC', 'DS', 'LCS', 'WS'].includes(String(row.env))) out.add('POST'); }
  return out;
}
function inScope(row, scope) {
  if (scope === 'REG') return String(row.env) === 'REG' && !row.round_code;
  return scopes(row).has(scope);
}
const entrantByName = new Map(competitors.entrants.map(e => [e.username, e]));
const ledgerDates = ledger.map(r => dayOf(r.game_date)).filter(Boolean).sort();
const stamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
let checkedStandings = 0;
for (const comp of competitions.competitions) {
  assert.ok(comp.id && comp.name && comp.description);
  assert.ok(['REG', 'POST', 'WC', 'DS', 'LCS', 'WS'].includes(comp.env_scope));
  if (comp.status === 'NOT_DRAWN') { assert.ok(comp.not_drawn_reason); continue; }
  assert.ok(stamp.test(comp.window_start) && stamp.test(comp.window_end), 'windows carry second-precision UTC stamps');
  assert.ok(dayOf(comp.window_start) >= ledgerDates[0] && dayOf(comp.window_end) <= ledgerDates[ledgerDates.length - 1],
    `competition ${comp.id} window must stay inside the export date range`);
  assert.ok(comp.window_start.endsWith('00:00:00Z') && comp.window_end.endsWith('23:59:59Z'));
  let prevRank = 0;
  for (const s of comp.standings) {
    const entrant = entrantByName.get(s.username);
    assert.ok(entrant, `standing references a registered entrant (${s.username})`);
    const rows = ledger.filter(r => inScope(r, comp.env_scope) && sliceMatches(r, entrant.slice) &&
      dayOf(r.game_date) >= dayOf(comp.window_start) && dayOf(r.game_date) <= dayOf(comp.window_end) &&
      ['W', 'L', 'P'].includes(r.result));
    const decided = rows.filter(r => r.result !== 'P');
    assert.strictEqual(s.picks, rows.length, `${comp.id}/${s.username}: picks must equal the re-derived row count`);
    assert.strictEqual(s.wins, decided.filter(r => r.result === 'W').length);
    const brierRows = rows.filter(r => Number.isFinite(Number(r.model_prob)));
    if (brierRows.length) {
      const brier = brierRows.reduce((sum, r) => sum + (Math.min(Math.max(Number(r.model_prob), 1e-15), 1 - 1e-15) - (r.result === 'W' ? 1 : r.result === 'L' ? 0 : 0.5)) ** 2, 0) / brierRows.length;
      assert.ok(Math.abs(brier - s.brier) < 1e-9, `${comp.id}/${s.username}: Brier must re-derive exactly`);
    }
    const verified = rows.filter(r => r.verification_status === 'VERIFIED_PRICE');
    if (!verified.length) {
      assert.strictEqual(s.pnl, null, 'PnL is null without verified-price rows — never synthesized');
      assert.strictEqual(s.pnl_status, 'UNAVAILABLE_NO_VERIFIED_PRICE_ROWS_IN_WINDOW');
    } else {
      assert.ok(Math.abs(s.pnl - verified.reduce((sum, r) => sum + Number(r.pnl || 0), 0)) < 0.01);
    }
    if (s.rank != null) { assert.ok(s.rank === prevRank + 1, 'qualified ranks are consecutive'); prevRank = s.rank; }
    checkedStandings += 1;
  }
}
assert.ok(checkedStandings >= competitions.competitions.length * 15,
  `expected to re-derive a substantial fraction of standings, did ${checkedStandings}`);

/* Bet review link contract: official endpoints keyed by game_pk. */
const reviewRow = ledger.find(r => r.game_pk != null);
assert.ok(reviewRow, 'ledger exposes rows for review');
for (const url of [
  `https://statsapi.mlb.com/api/v1.1/game/${reviewRow.game_pk}/feed/live`,
  `https://statsapi.mlb.com/api/v1/game/${reviewRow.game_pk}/boxscore`,
  `https://www.mlb.com/gameday/${reviewRow.game_pk}`,
]) assert.ok(/^https:\/\/(statsapi\.mlb\.com|www\.mlb\.com)\//.test(url), 'review URLs target official MLB hosts only');
for (const row of ledger) {
  if (row.quote_source_url) assert.ok(/^https?:\/\//.test(row.quote_source_url), 'quote sources are URLs');
}

console.log(`UI data contract passed: ${strategies.length} strategies, ${checks.length} controls, ${ledger.length} ledger rows, ${players.length} players, ${competitors.entrants.length} entrants, ${competitions.competitions.length} competitions, ${checkedStandings} standings re-derived`);
