# Data issues and corrections

MLBComp treats discrepancies as first-class records. `data_issues` records an
issue ID, type, severity, status, entity, description, sources and correction
link. Historical records are not silently rewritten.

## Current known limitations

1. **SOURCE_SNAPSHOT is committed; parquet is not.** `data/raw/` and
   `data/features/` are gitignored. Export refuses to wipe the snapshot
   without `--force`.
2. **Historical quote timestamps may be unavailable.** A price joined by game
   ID is retained for research but is not eligible for paper PnL until its
   availability time is verified. cesar-dx rows stay UNVERIFIED.
3. **Optional data families are source-dependent.** Probable/confirmed
   starters, lineups, injuries, weather forecasts, umpire assignments,
   Statcast, player props, live order books and Kalshi contracts remain
   `NOT_VERIFIED` until observed.
4. **Postseason samples are small.** Wild Card, DS, LCS and World Series are
   separate, and no round is assumed easier or harder to predict.
5. **Experiment aliases double-counted POST totals** until 2026-09-22. Unique
   totals now exclude `MLB_POST_MODEL_A`–`E`. Raw totals are retained.
6. **MODEL_C binding mismatch.** This snapshot's `MLB_POST_MODEL_C_001`
   metrics were produced by hierarchical; EXP_C evaluated series-state.
   Future runs bind MODEL_C to dedicated postseason Elo. Metrics are not rewritten.
7. **Egress.** statsapi.mlb.com, retrosheet.org, baseballsavant, weather.gov
   and Kalshi TLS-fail from this environment (re-checked 2026-09-22). GitHub
   remains reachable.

## Resolution policy

- Missing data: `DATA_UNAVAILABLE`, `NOT_RUN` or `PROPOSED`.
- Conflicts: preserve both source observations, open an issue, and quarantine
  the derived field until resolved.
- Bad settlement or execution: append a correction event linked to the prior
  hash; never edit an immutable wager event.
- Strategy changes: create a new version with parent and change summary.
- Source reachability is not data verification; content checks and licensing
  are separate gates.
