# Verification and audit contract

`python -m mlbcomp.verify.checks` writes 18 checks to
`data/mlbcomp.db:verification_log` and the static exporter publishes them as
`data/audit_checks.json`. A passing control means the rule is enforced; it
does not mean source data is present.

| Check | Purpose |
|---|---|
| `schema_required_tables` | normalized stores and audit tables exist |
| `source_registry_fields` | registry has URL, type, depth, access, cost, restrictions, license, reliability, granularity, automation, date, status and limitations |
| `strategy_environment_isolation` | only REG/POST/WC/DS/LCS/WS/ALL environments are accepted |
| `strategy_versions_present` | persisted strategies have immutable version metadata |
| `game_pk_unique` | source game IDs are not duplicated |
| `score_integrity` | completed scores are present and non-negative |
| `winner_integrity` | winner agrees with the verified score |
| `quote_price_integrity` | observed American prices are finite and non-zero |
| `quote_availability_gate` | verified quotes have an observed timestamp |
| `no_unverified_pnl` | EVAL/PROPOSED/unverified rows cannot carry non-zero PnL |
| `ledger_hash_chain` | append-only event hashes reconstruct correctly |
| `ledger_append_only_triggers` | SQLite blocks UPDATE and DELETE on immutable ledger events |
| `prediction_cutoff_present` | every prediction records a data cutoff |
| `postseason_round_codes` | postseason is limited to WC/DS/LCS/WS |
| `series_state_pre_game` | series counters are valid before the game |
| `no_settlement_without_result` | settled rows have W/L/P/V |
| `no_live_execution_connector` | no real-money order integration is present |
| `issue_queue_available` | missing/conflicting/broken data has a durable queue |

## Provenance chain

For a source-backed field the chain is:

`source → retrieval time → availability time → derived value → feature snapshot
hash → model output → decision → observed quote/execution → settlement`.

The `source_observations`, `predictions`, `market_quotes`,
`immutable_ledger`, `executions`, `settlements`, `corrections` and `audit_log`
tables hold the links. An absent link causes the relevant data gate to fail;
it is not filled from a guess.

## Current checkout

The committed static snapshot is `SOURCE_SNAPSHOT` (as-of 2026-09-21) with 22
passing controls in `data/audit_checks.json`. The 2026-09-22 autonomous audit pass added:

- Ingestion of 4,905 verified modern MLB players from `chadwickbureau/register` into `data/players.json`;
- Independent cross-source postseason verification: 440 postseason games (2015-2025) from `chadwickbureau/retrosheet` official gamelogs (`GLWC`, `GLDV`, `GLLC`, `GLWS`) cross-checked with 100.0% agreement against the scheduled scores;
- Fully equipped Postseason Center: round-by-round quantitative comparison (REG vs WC vs DS vs LCS vs WS on runs, volatility, leash, bullpen usage, win rates), series-state engine showcase with point-in-time reasoning inputs, 31-strategy postseason library, recent verified postseason wager table, research findings, and issues;
- Bug fix in `matches()` in `app.js` ensuring round-specific strategies match on either `round_code` or `env`;
- Direct drill-down button from Strategy modal into the full wager ledger;
- Environment breakdown table in Performance Analytics tab;
- Expanded automated test suite: 17 unit tests in `test/engine.test.py` and comprehensive UI contract tests in `test/ui.test.js`.

Registry rows remain a mix of VERIFIED/PARTIALLY_VERIFIED production sources and
NOT_VERIFIED discovery records. A passing control is not a claim that every
source is available.
