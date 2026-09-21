/**
 * MLBComp UI & Data Contract Test Suite
 * Validates:
 * - JSON schema validity of all published competition data
 * - Leaderboard ranking and numeric fields
 * - Upcoming bets status flags and active slate
 * - Registry completeness (>= 30 sources)
 * - Research experiments integrity
 * - Audit checks 18/18 PASSED
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');

function runTests() {
  console.log('Running MLBComp UI & Data Test Suite...');

  // 1. Check summary.json
  const summaryPath = path.join(__dirname, '..', 'data', 'summary.json');
  assert.ok(fs.existsSync(summaryPath), 'data/summary.json must exist');
  const summary = JSON.parse(fs.readFileSync(summaryPath, 'utf8'));
  assert.strictEqual(summary.current_season, 2026);
  assert.ok(summary.total_strategies >= 50, 'At least 50 strategies tracked');
  assert.ok(summary.total_games_tracked > 25000, 'Over 25,000 tracked MLB games');
  console.log('✓ summary.json validated');

  // 2. Check leaderboard.json
  const leaderboardPath = path.join(__dirname, '..', 'data', 'leaderboard.json');
  assert.ok(fs.existsSync(leaderboardPath), 'data/leaderboard.json must exist');
  const leaderboard = JSON.parse(fs.readFileSync(leaderboardPath, 'utf8'));
  assert.ok(leaderboard.length >= 50, 'Leaderboard must contain all strategy personas');
  for (const s of leaderboard) {
    assert.ok(s.id, 'Strategy must have id');
    assert.ok(s.username.startsWith('@'), 'Username must start with @');
    assert.strictEqual(typeof s.total_pnl, 'number');
    assert.strictEqual(typeof s.roi, 'number');
    assert.strictEqual(typeof s.win_rate, 'number');
    assert.ok(Array.isArray(s.equity_curve), 'Strategy must have equity curve');
    assert.ok(s.current_bankroll === Number((s.initial_bankroll + s.total_pnl).toFixed(2)), 'Bankroll math must match');
  }
  console.log(`✓ leaderboard.json validated (${leaderboard.length} strategies, mathematical bankroll parity verified)`);

  // 3. Check upcoming_bets.json
  const upcomingPath = path.join(__dirname, '..', 'data', 'upcoming_bets.json');
  assert.ok(fs.existsSync(upcomingPath), 'data/upcoming_bets.json must exist');
  const upcoming = JSON.parse(fs.readFileSync(upcomingPath, 'utf8'));
  assert.ok(upcoming.length > 0, 'Must have active upcoming signals');
  for (const u of upcoming) {
    assert.ok(u.bet_id, 'Upcoming bet must have bet_id');
    assert.ok(u.matchup, 'Must have matchup');
    assert.strictEqual(u.status, 'READY_TO_BET');
  }
  console.log(`✓ upcoming_bets.json validated (${upcoming.length} upcoming signals)`);

  // 4. Check registry.json
  const regPath = path.join(__dirname, '..', 'data', 'registry.json');
  assert.ok(fs.existsSync(regPath), 'data/registry.json must exist');
  const registry = JSON.parse(fs.readFileSync(regPath, 'utf8'));
  assert.ok(registry.length >= 30, 'Registry must have at least 30 sources');
  for (const r of registry) {
    assert.ok(r.name, 'Source must have name');
    assert.ok(r.url, 'Source must have url');
    assert.ok(r.reliability_rating, 'Must have reliability rating');
  }
  console.log(`✓ registry.json validated (${registry.length} data sources)`);

  // 5. Check irregularities.json
  const irrPath = path.join(__dirname, '..', 'data', 'irregularities.json');
  assert.ok(fs.existsSync(irrPath), 'data/irregularities.json must exist');
  const irr = JSON.parse(fs.readFileSync(irrPath, 'utf8'));
  assert.ok(irr.length >= 5, 'Must have documented irregularities');
  console.log(`✓ irregularities.json validated (${irr.length} irregularities tracked)`);

  // 6. Check research_experiments.json
  const expPath = path.join(__dirname, '..', 'data', 'research_experiments.json');
  assert.ok(fs.existsSync(expPath), 'data/research_experiments.json must exist');
  const experiments = JSON.parse(fs.readFileSync(expPath, 'utf8'));
  assert.ok(experiments.length >= 5, 'Must have research experiments');
  console.log(`✓ research_experiments.json validated (${experiments.length} experiments)`);

  // 7. Check audit_checks.json
  const auditPath = path.join(__dirname, '..', 'data', 'audit_checks.json');
  assert.ok(fs.existsSync(auditPath), 'data/audit_checks.json must exist');
  const audits = JSON.parse(fs.readFileSync(auditPath, 'utf8'));
  assert.strictEqual(audits.length, 18, 'Must have 18 audit checks');
  assert.ok(audits.every(a => a.passed === true), 'All 18 audit checks must PASS');
  console.log(`✓ audit_checks.json validated (18/18 checks PASSED)`);

  console.log('\nALL MLBCOMP UI & DATA CONTRACT TESTS PASSED SUCCESSFULLY! ✅');
}

runTests();
