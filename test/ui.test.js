/** Static-site data contract tests. The safe empty snapshot is valid. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const root = path.join(__dirname, '..', 'data');
const read = name => JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'));

const summary = read('summary.json');
assert.ok(['NO_SOURCE_SNAPSHOT', 'SOURCE_SNAPSHOT'].includes(summary.data_mode));
assert.ok(summary.total_strategies >= 50, 'strategy research library is present');
assert.ok(!('total_simulated_pnl' in summary) || summary.total_simulated_pnl === null || typeof summary.total_simulated_pnl === 'number');

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
console.log(`UI data contract passed: ${strategies.length} strategies, ${checks.length} controls, ${ledger.length} ledger rows`);
