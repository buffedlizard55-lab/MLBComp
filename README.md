# MLBComp — autonomous MLB research and paper competition

MLBComp is an auditable research platform for the loop:

**research → discover → verify → model → backtest → forward-test → paper trade → analyze → improve**

It never places real-money bets. There is no bookmaker, exchange or Kalshi
order-placement client in this repository.

## What is implemented

- Separate `REG`, `POST`, `WC`, `DS`, `LCS`, `WS` and `ALL` environments.
  `ALL` is a view that retains the environment breakdown; it is not a blended
  score that hides postseason results.
- A 68-entry versioned research catalog spanning baselines, starting pitchers,
  bullpens, lineups/injuries, Statcast, pitch mix, park/weather, umpires,
  rest/travel, market movement, totals, first-five, run line, team/player
  props, live, futures, exchange and prediction-market hypotheses.
- Postseason transfer, adjusted, dedicated, series-state, hierarchical and
  round-specific model slots. Wild Card, Division Series, LCS and World Series
  have independent strategy/model versions.
- Point-in-time feature and series-state contracts. Current series state is
  computed before the game; a result is applied only after every prediction
  for that game has been made.
- Chronological train/validate/out-of-sample utilities, Brier score, log loss,
  calibration tables, PnL/CLV math, sample-size gates and experiment records.
- A normalized SQLite store for games, players, rosters, lineups, starters,
  bullpen usage, injuries, statistics, Statcast, weather, umpires, travel,
  markets, quotes, models, strategies, predictions, forward tests, positions,
  settlements, issues and audits.
- An append-only, SHA-256 hash-chained paper ledger. Corrections are linked
  records; immutable wager events cannot be updated or deleted.
- A source registry with the requested fields: URL, data type, historical
  depth, current availability, access method, cost, restrictions, license,
  reliability, granularity, automation capability, verification date, status
  and limitations.
- A static GitHub Pages competition site with dashboard, separate round
  center, filterable leaderboards, strategy drill-downs, upcoming predictions,
  paper positions, ledger, analytics, research, source registry and audit
  queue.

## Truthful data policy

This checkout currently publishes a `SOURCE_SNAPSHOT` JSON projection. On a
checkout where `data/raw/` and `data/features/` are absent, `python -m
mlbcomp.web.export_static` **refuses** to overwrite that snapshot unless
`--force` is passed, so a catalog-only rebuild cannot silently erase verified
numbers. With `--force`, the site says `NO_SOURCE_SNAPSHOT`: it shows the
catalog and controls, but no game, lineup, price, fill, result, odds,
liquidity, PnL or performance is asserted.

`MLB_POST_MODEL_A`–`E` are experiment aliases of catalog strategies. Dashboard
totals use `unique_environment_breakdown` / `unique_verified_pnl` so POST PnL
is not double-counted. Raw `environment_breakdown` is retained for audit.

A historical quote is eligible for a wager only when it has:

1. a source observation and URL/API locator;
2. retrieval and availability timestamps;
3. a valid price and, where relevant, bid/ask and available size; and
4. verification status that passes the market data gate.

A game result without a verified price is an `EVAL` record. A future or
incomplete game is `PROPOSED` (or `OPEN` only after a paper execution record).
No +100 placeholder, synthetic total, assumed fill, invented historical line,
liquidity or Kalshi trade is used.

## Rebuild / operate

Use a virtual environment, then:

```bash
python -m pip install -r requirements.txt
python -m mlbcomp.engine.catalog
python -m mlbcomp.sources                         # inspect source registry
python scripts/fetch_sources_github_api.py        # opt-in, writes data/raw/
python -m mlbcomp.ingest.baseballr                # validate and normalize source data
python -m mlbcomp.features.po_corpus              # source-backed postseason corpus
python -m mlbcomp.engine.backtest                 # chronological competition
python -m mlbcomp.engine.research                  # questions + Models A–E
python -m mlbcomp.verify.checks                    # 18 adversarial controls
python -m mlbcomp.web.export_static                # publish data/*.json
python -m http.server 8000 --bind 0.0.0.0          # GitHub Pages-style preview
```

The fetcher records content-addressed file metadata in
`data/raw/FETCH_MANIFEST.json`. Raw files, parquet features and SQLite are
ignored by Git; the static JSON projection is the only committed data output.
If a source cannot be reached or verified, the issue queue records it and the
pipeline continues without using it.

## Models and strategy contract

Every strategy defines a hypothesis, data requirements, entry rule, required
price rule, sizing, settlement, test plan and limitations. A model is not
promoted from its catalog merely because it is complex. The permanent
postseason comparison is:

- **A:** regular-season model transferred unchanged;
- **B:** regular model plus postseason adjustment;
- **C:** dedicated postseason model/series state;
- **D:** separate round models;
- **E:** hierarchical partial pooling.

Career/multi-year information can be a postseason prior; current-season,
late-season, prior-postseason and current-postseason information update it only
when available before the decision. No later series game enters an earlier
prediction.

## Tests

```bash
python test/engine.test.py
node test/ui.test.js
```

GitHub Actions runs both suites and checks JavaScript syntax on pushes and pull
requests. The tests cover the static data contract, ledger hash chain, quote
provenance and selection gates, timezone-aware decision cutoffs, finite risk
limits, fair-odds round trips, settlement outcomes and duplicate rejection,
no-unverified-PnL rule, strategy catalog, and round/environment isolation. The
safe `NO_SOURCE_SNAPSHOT` export is a valid test state.

## Known limitations and next priorities

- The committed JSON is a SOURCE_SNAPSHOT from 2026-09-21. `data/raw/` and
  `data/features/` are gitignored, so this sandbox cannot re-run ingest/backtest.
  Do not treat a missing parquet tree as a reason to wipe the published numbers.
- Source coverage, historical quote timestamps, lineups, injuries, weather,
  umpire assignments, player props, live markets and Kalshi order-book history
  are source-dependent and remain `NOT_VERIFIED` until explicitly observed.
- Next priorities are checksum-pinned source fetches, independent cross-source
  joins, timestamped historical quotes, starter/lineup availability records,
  bullpen workload features, and walk-forward forward-testing. Small samples
  remain insufficient for an edge verdict.

## Paper API validation

`record_prediction` requires a timezone-aware ISO-8601 decision timestamp and
rejects a supplied data cutoff later than the decision. `record_wager` additionally
requires verified prediction availability, a source ID and URL/API locator,
a matching quote selection, a catalog market, finite positive bankroll/stake,
and quote observation ≤ availability ≤ decision. UTC offsets are normalized for
comparison. Paper execution time is the decision time, not the earlier quote
observation time. Invalid input is rejected before wager/position writes.

These are input-contract checks, not independent verification of a source's
contents or proof of executable liquidity. Existing historical exports are not
rewritten by these API changes. In particular, previously exported required-price
values must be regenerated from their source snapshot to incorporate the corrected
probability-to-American-odds conversion; this checkout has no raw source snapshot
with which to do that reproducibly.
