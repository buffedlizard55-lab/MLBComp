# Data issues and corrections

MLBComp treats discrepancies as first-class records. `data_issues` records an
issue ID, type, severity, status, entity, description, sources and correction
link. Historical records are not silently rewritten.

## Current known limitations

1. **No source snapshot in the committed checkout.** The site is a safe
   catalog/control view. Run the fetch and validation pipeline before a model
   result is interpreted.
2. **Historical quote timestamps may be unavailable.** A price joined by game
   ID is retained for research but is not eligible for paper PnL until its
   availability time is verified.
3. **Optional data families are source-dependent.** Probable/confirmed
   starters, lineups, injuries, weather forecasts, umpire assignments,
   Statcast, player props, live order books and Kalshi contracts remain
   `NOT_VERIFIED` until observed.
4. **Postseason samples are small.** Wild Card, DS, LCS and World Series are
   separate, and no round is assumed easier or harder to predict.

## Resolution policy

- Missing data: `DATA_UNAVAILABLE`, `NOT_RUN` or `PROPOSED`.
- Conflicts: preserve both source observations, open an issue, and quarantine
  the derived field until resolved.
- Bad settlement or execution: append a correction event linked to the prior
  hash; never edit an immutable wager event.
- Strategy changes: create a new version with parent and change summary.
- Source reachability is not data verification; content checks and licensing
  are separate gates.
