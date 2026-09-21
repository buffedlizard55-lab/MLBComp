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

The committed static snapshot is intentionally `NO_SOURCE_SNAPSHOT` until the
fetcher and validators are run. It therefore contains no historical wager or
performance claim. Registry rows are discovery records with
`NOT_VERIFIED` status. This is safer than presenting a plausible but
unreproducible scorecard.
