# Final Report — 2026-09-21 — MLBComp Autonomous Research & Paper Competition

**Branch:** `arena/01a0c627-mlbcomp` · **As of:** `2026-09-21` · **Data mode:** `SOURCE_SNAPSHOT` · **Generated:** `2026-09-21T22:53:49Z`

This report separates **verified facts**, **calculations**, **assumptions**, and **conclusions**. No real-money bets were placed. All positions are paper-only.

---

## 1. What was built

A complete autonomous MLB research → model → backtest → forward-test → paper-trade → audit loop:

* **Environments:** `REG`, `POST`, `WC`, `DS`, `LCS`, `WS`, `ALL` (ALL is a roll-up view, not a blended score).
* **68 versioned strategy hypotheses** persisted with immutable `strategy_versions` and `model_versions` (rules, data, features, params, test plan, limitations, parent, change summary).
* **Point-in-time feature engine** (Elo REG/POST split, 30-game and season rollups, late-September rollup, play-by-play aggregates, series-state pre-game).
* **Chronological backtest** with gate: verified timestamped quote required for stake/PnL; otherwise `EVAL` (calibration only) or `PROPOSED` (future/incomplete). No synthetic +100.
* **Hash-chained append-only ledger** (`immutable_ledger` + SQLite `UPDATE`/`DELETE` triggers, `verify_chain`).
* **Source registry** with required fields and verification observations; content-addressed `FETCH_MANIFEST`.
* **Static GitHub Pages site** (dashboard, leaderboards, Postseason Center, Strategy Lab, upcoming, positions, ledger, analytics, research, sources, verification, methodology) filtering by env/round/market/team/player/game/model.
* **Verification:** 18 adversarial controls, `verification_log` + `data_issues` queue.
* **Export:** `mlbcomp.web.export_static` projection (`data/*.json`) without leaking future info.

**Verification status:** `python test/engine.test.py` OK (4 tests), `node test/ui.test.js` OK (68 strategies, 18 controls, 20000 ledger rows capped).

---

## 2. Data sources discovered / verified / rejected

**Discovery catalog (15 entries, `mlbcomp/sources.py`):**

| source_id | URL | data_type | verification_status | availability | notes |
|---|---|---|---|---|---|
| `sportsdataverse_baseballr` | `https://github.com/sportsdataverse/baseballr-data` | schedules, schedules, play-by-play parquet | **PARTIALLY_VERIFIED** | AVAILABLE 2026-09-21 | Fetched via `api.github.com` git-blobs (31 files, content-addressed). Historical depth 2015-2026. License: repo license applies. Limitation: lineups not in this mirror; scores are source observation. |
| `cesar_dx_mlb_odds` | `https://github.com/cesar-dx/mlb-betting-ml` | historical moneyline + win-pct + Statcast-derived features | **PARTIALLY_VERIFIED** | AVAILABLE | Fetched 7 CSV (2019-2025). `home_odds`/`away_odds` present for 94.5% of joined games, but **no `observed_at`/`available_at`/`closing_flag`**, so `verification_status=UNVERIFIED`, `market_quote_verified=False`. Not eligible for paper PnL. |
| `mlb_stats_api` | `https://statsapi.mlb.com/api/v1/` | schedules, rosters | NOT_VERIFIED | NOT_CHECKED | Egress blocked (`statsapi.mlb.com` refused) in sandbox; not used as dependency. |
| `baseballsavant_statcast` | `https://baseballsavant.mlb.com/` | pitch-level Statcast | NOT_VERIFIED | NOT_CHECKED | Not fetched; `DATA_UNAVAILABLE` strategies rely on it. |
| `retrosheet` | `https://www.retrosheet.org/` | historical logs | NOT_VERIFIED | NOT_CHECKED | Not automated. |
| `pybaseball` | `https://github.com/jldbc/pybaseball` | wrapper | NOT_VERIFIED | — | Not independent corroboration. |
| `fangraphs`, `baseball_reference` | — | advanced/reference | NOT_VERIFIED | low automation | Not auto-ingested. |
| `noaa_ncei`, `weather_gov` | — | weather | NOT_VERIFIED | NOT_CHECKED | Venue join not yet validated. |
| `umpire_scorecards` | — | umpire | NOT_VERIFIED | — | Assignment timestamp missing. |
| `mlb_schedule_official` | — | schedule/probables | NOT_VERIFIED | — | Probable ≠ confirmed. |
| `kalshi_api` | `https://trading-api.kalshi.com/trade-api/v2/` | contracts/quotes/trades | NOT_VERIFIED | UNAVAILABLE | Optional only; no fills invented. |
| `github_search`, `academic_search` | — | discovery | NOT_VERIFIED | — | Discovery only. |

**Fetch method verified:** `raw.githubusercontent.com` and `statsapi.mlb.com` refuse in this sandbox; `api.github.com` (200 OK) + `/git/blobs/{sha}` base64 path works. Every fetched byte is logged in `data/raw/FETCH_MANIFEST.json` with `blob_sha`, `bytes`, `dest`, `fetched_utc`. Post-ingest SHA not yet pinned per-file (recorded as `RETRIEVED` observation, checksum stored).

**Ingest result ( `python -m mlbcomp.ingest.baseballr` 74s ):**
* `games=28072` (27632 REG, 440 POST), `po_games=440`, `series=113`, `state=440`, `odds_joined=15442` (dropped 0 on team/date cross-check), `pitcher_game.parquet` + `game_events.parquet` built. Teams, seasons, series_state written to SQLite.

**Rejected / not yet production:** Statcast pitch-level, weather, umpire, travel, lineup/injury, market open/current/close timestamps, Kalshi order book — all remain `DATA_UNAVAILABLE` or `NOT_VERIFIED` until a source proves timestamp, license, and automation. No placeholder is created.

---

## 3. Regular-season models

* `elo` (time-decayed Elo, home +20, K=20 with MOV boost) — baseline, `MLB_REG_ELO_001`
* `form` (30-game run-diff) — `MLB_REG_FORM_001`
* `season` (expanding season RD) — `MLB_REG_SEASON_001`
* `lateform` (September RD) — `MLB_REG_LATEFORM_001`
* `poisson_total` (Poisson run-rate totals, line is model hypothesis, never confused with market) — `MLB_REG_TOTALS_001`
* `xreg` / `market` controls

All use **only** information with `availability_time <= decision_time`. REG and POST Elo trackers are separate; POST tracker inherits REG rating as prior at first postseason appearance, then evolves only from postseason results.

---

## 4. Postseason models

* **A Transfer:** `MLB_POST_XREG_001` (`xreg` = REG Elo unchanged)
* **B Adjusted:** `MLB_POST_ADJUSTED_001` (`round_specific` intercept learned only from prior postseason seasons)
* **C Dedicated / Series-state:** `MLB_POST_SERIESSTATE_001` + `MLB_POST_HIERARCHICAL_001`
* **Hierarchical partial pooling:** `model_hierarchical` — `career/multi-year (Elo) → current season → late season → prior postseason → current postseason (shrinkage via `weight = min(0.8,0.15+0.20*n_series_games)` plus prior `p_elo`).
* **Round-specific intercept:** `model_round_specific` — `p = sigmoid(logit(p_post_elo) + intercept[round])`, intercept clipped ±0.25 and learned only from completed postseason seasons with n≥15 per round.

Series-state inputs (round, series_key, game_number, wins_a/b_before, elimination/clinch, games_remaining, days_rest, travel_km, home_adv_games_left) are **computed before the game**; update occurs after every model's decision for that game.

---

## 5. Round-specific models

For each of `WC, DS, LCS, WS`:

* `MLB_POST_{R}_ELO_001` — round-specific Elo intercept
* `MLB_POST_{R}_HIER_001` — hierarchical partial pooling
* `MLB_POST_{R}_STATE_001` — series-state (only triggers on clinch/elimination games)
* `MLB_POST_{R}_MARKET_001` — market (`DATA_UNAVAILABLE` until verified quotes)

Plus four `PITCHING_001`, `BULLPEN_001`, `LINEUP_001`, `MANAGER_001` hypotheses intentionally `DATA_UNAVAILABLE` until pitch-level/bullpen/lineup sources are verified.

**Fact:** Wild Card 2012/2015 = 1 win; 2020 & 2022+ = 2 wins; DS = 3 wins every year (2020 expanded only the WC, not the DS); LCS/WS = 4. Implemented in `round_needed()` and `group_series()`.

---

## 6. Strategies created

**68 versioned entries** (`mlbcomp/engine/strategies.py` `build_catalog`):

* **REG 38:** Elo, form, season, lateform, Poisson totals, favorite-price test, plus 32 `DATA_UNAVAILABLE` covering starter/pitch-mix/bullpen-fatigue/leverage/lineup/platoon/injury/Statcast/defense/weather/park/umpire/rest/scheduling/market-move/closing/F5/RL/team-total/player-K/hit/alt/live/exchange/futures/NRFI/YRFI/inning/prediction-market.
* **POST 30:** 14 all-post (xreg, adjusted, hierarchical, series-state, pitching/bullpen/lineup/manager/market) + 16 round-specific + 5 permanent comparison experiments `MLB_POST_MODEL_{A,B,C,D,E}_001` (never assumed superior).

Every entry defines `hypothesis → data_requirements → entry_rule → required_price_rule → sizing_rule (quarter-Kelly cap 5%) → settlement_rule → test_plan → limitations`. Example pricing note in export: *No verified quote means EVAL/PROPOSED only; PnL and ROI remain null.*

Duplicate/overwrite protection: `INSERT OR IGNORE` on `strategies`/`strategy_versions`; new model is new version.

---

## 7. Backtests

**Command:** `python -m mlbcomp.engine.backtest` → `backtest:0cb05cfaa6e7` (`min_season=2015`, `strategies=27` runnable out of 68).

* **Chronological:** games sorted by `start_ts, game_pk`; features computed, then all runnable models decide, then series state updated with the verified result. Postseason Game 3 sees Games 1–2 only.
* **Price gate:** `_market_quote` requires `market_quote_verified=True`, `source_id` in verified observations or `source_observation_id` in verified set, `observed_at` & `available_at` ≤ `start_ts`, `market_quote_verified` flag; otherwise no quote → `EVAL` if result exists, `PROPOSED` if future/incomplete. No synthetic odds.
* **Counts:** `settled_bets=0`, `eval=118,214`, `proposed=25,884`, `open=0`, `skipped=0`. Round intercepts learned: `WC -0.058, DS +0.029, LCS 0.0, WS 0.0` (prior-season only, n≥15, clipped ±0.25).
* **Outputs:** `bets` (144,098 rows), `predictions` (144,098), `calibration` (26 rows — one per evaluated strategy), `bankroll` snapshots (derived, not a performance claim), `backtest_runs` entry.

**Verified fact:** No wager received `VERIFIED_PRICE` because `odds.parquet` rows are `UNVERIFIED` (no timestamp) → stake = 0, pnl = NULL, `verification_status=NO_MARKET_PRICE`. This is intentional safety, not a failed backtest.

---

## 8. Forward tests

**Predictions table** holds 144,098 point-in-time predictions with `decision_time`, `data_cutoff_time`, `feature_snapshot_hash`, `source_observation_ids`, `availability_status=SOURCE_BACKED`.

**Upcoming projection:** `python -m mlbcomp.web.export_static` now filters to `game_date >= TODAY` & `home_score IS NULL` → **441 upcoming predictions for 91 scheduled 2026 REG games** (2026-09-21 to 2026-09-27). Historic cancelled voids (9 games: CR/CA/CO/CI/C9 in 2015-2021,2024) are excluded from the upcoming view (remain `PROPOSED` in DB but not shown as forward proposals).

**Forward-test table:** `forward_tests` exists for the `mlbcomp.engine.forward.record_forward_test` API; current run did not insert forward rows (predictions are the forward-test evidence). Stake/pnl remains NULL until a verified observed quote with `VERIFIED` source_observation is supplied — enforced by `_market_quote` and `record_forward_test` guards.

---

## 9. Paper-trading results

* **Engine:** `mlbcomp.engine.ledger.record_wager` requires `ObservedQuote.verification_status=VERIFIED`, non-zero finite price, `observed_at`/`available_at` ≤ `decision_time`, positive stake ≤ 5% bankroll. Hash-chained `immutable_ledger` with `previous_hash`/`entry_hash`; triggers block `UPDATE`/`DELETE`.
* **This snapshot result:** **0 verified wagers**, **0 open positions**, **0 settlements**, **0 immutable ledger rows** with `market=ML`/`PREDICTION_MARKET` (ledger check: `rows=0, valid=True`). Reason: historical odds lack timestamps → gate correctly blocks fills, liquidity, slippage, closing price.
* **Test evidence:** `python test/engine.test.py` demonstrates engine correctness with synthetic verified quotes (paper-only, not part of the historical snapshot): `record_prediction` → `record_wager` → `settle_wager` → `verify_chain` passes; unverified quote correctly raises.

**Conclusion:** No paper PnL is claimed for this history. Forward paper trading is the correct path until timestamped historical quotes are verified.

---

## 10. Regular-season performance

*Source: `calibration` + `bets` (n = completed REG games). No verified PnL.*

| strategy | n_games | brier | log_loss | win_rate* | verified ROI |
|---|---|---|---|---|---|
| MLB_REG_ELO_001 | 27532 | 0.2590 | 0.7142 | 55.4%† | — (no verified price) |
| MLB_REG_FORM_001 | 27455 | 0.3337 | 1.0454 | ~52% | — |
| MLB_REG_SEASON_001 | 25652 | 0.3143 | 0.9347 | — | — |
| MLB_REG_LATEFORM_001 | 25558 | 0.3555 | 1.2240 | — | — |
| MLB_REG_FAV_BIAS_001 | 7361 | 0.2597 | 0.7175 | — | — |
| MLB_REG_TOTALS_001 | 0 evaluated (TOTAL needs observed line) | — | — | — | — |

*† win_rate = W/(W+L) for ML picks where `p >= 0.5` → HOME. Not a market edge.*

**Calculation:** Brier = mean((p - y)^2), y=1 if HOME win else 0 for ML. Log loss clipped ε=1e-15.

**Assumption:** `p_elo` is calibrated baseline; `form`/`lateform` appear worse than Elo in this history (higher Brier).

**Verified fact:** `REG: strategies=38, records=139,442, evaluation_picks=113,558, verified_bets=0, status=NO_VERIFIED_MARKET_PRICE`.

---

## 11. Wild Card performance

* **Dedicated models:** `MLB_POST_WC_ELO_001` (n=67, brier 0.2502), `MLB_POST_WC_HIER_001` (n=67, 0.2519), `MLB_POST_WC_STATE_001` (n=33 elim/clinch only, 0.2239)
* **Source rows:** 67 WC games (2015-2025), 1 per early years, 2-win format from 2022+. Evaluation only, no verified price.
* **Finding:** `Q03` home win 53.6% POST vs 53.3% REG Δ+0.003 inconclusive; `Q10` elimination vs non-elim Δ≈0.0018 inconclusive.

---

## 12. Division Series performance

* **Models:** `MLB_POST_DS_ELO_001` (n=181, brier 0.2494), `MLB_POST_DS_HIER_001` (0.2512), `MLB_POST_DS_STATE_001` (n=56, 0.2698)
* **Rows:** 181 DS games, `DS: strategies=4, records=418, evaluation=418, verified=0`.

---

## 13. LCS performance

* **Models:** `MLB_POST_LCS_ELO_001` (n=126, 0.2533), `MLB_POST_LCS_HIER_001` (0.2553), `MLB_POST_LCS_STATE_001` (n=24, 0.2750)
* **Rows:** 126 LCS games, `LCS: strategies=4, records=276, evaluation=276, verified=0`.

---

## 14. World Series performance

* **Models:** `MLB_POST_WS_ELO_001` (n=66, 0.2607), `MLB_POST_WS_HIER_001` (n=66, 0.3093), `MLB_POST_WS_STATE_001` (n=15, 0.3501) — small-sample, high Brier.
* **Rows:** 66 WS games, `WS: strategies=4, records=147, evaluation=147, verified=0`.

---

## 15. Regular vs postseason model comparison (Models A–E)

Permanent experiment, **no superiority assumed**:

| id | name | n | brier | log_loss | verified ROI | win_rate |
|---|---|---|---|---|---|---|
| EXP_A | Model A — regular-season transfer (`xreg`) | 440 | **0.2494** | 0.6921 | — | 54.8% |
| EXP_B | Model B — regular + postseason adjustment (`round_specific` intercept) | 440 | 0.2549 | 0.7034 | — | 50.2% |
| EXP_C | Model C — dedicated postseason/series-state (`hierarchical` clinch/elim only) | 128 | 0.2574 | 0.7162 | — | 53.9% |
| EXP_D | Model D — round-specific Elo (WC/DS/LCS/WS pooled) | 440 | 0.2549 | 0.7034 | — | 52–55%* |
| EXP_E | Model E — hierarchical partial pooling | 440 | 0.2564 | 0.7096 | — | 54.3% |

*D aggregates `MLB_POST_WC_ELO_001`, `DS_ELO_001`, `LCS_ELO_001`, `WS_ELO_001`.*

**Calculation:** `brier_score(p, y)` on POST ML picks where model produced `p`.

**Assumption:** All models scored on same 440 POST completed games; no market filter.

**Conclusion:** Transfer (A) has lowest Brier in this snapshot (0.249), but delta vs B/E (≈0.005–0.007) is **INCONCLUSIVE** with n=440 and no market PnL; `REQUIRES_REPLICATION`. No model is promoted.

Research findings echo: `Q15–Q18` remain `NOT_RUN` for market ROI (needs verified quotes); descriptive run-total/margin findings are `REQUIRES_REPLICATION`.

---

## 16. Current competition status

* **Data mode:** `SOURCE_SNAPSHOT` (verified fetch + ingest + backtest + research + checks + export).
* **Health:** 18/18 controls PASS (duplicate game_pk 0, score integrity 27972 completed, quote price integrity 0 bad, no_unverified_pnl 0, hash chain 0 rows valid, trigger ok, series_state pre-game ok).
* **Games:** 28,072 tracked, 27,972 completed, 100 upcoming (91 scheduled future 2026 + 9 historic cancelled voids).
* **Strategies:** 68 total, 27 runnable in this snapshot (6 REG ML + Poisson totals + 21 POST incl. round-specific).
* **Records:** 144,098 predictions/bets, 118,214 EVAL, 25,884 PROPOSED, 0 SETTLED (no verified price).
* **Leaderboard:** Separate REG/POST/WC/DS/LCS/WS slices; all `NO_VERIFIED_MARKET_PRICE` (no ROI claimed).
* **Upcoming:** 441 forward proposals for 91 future 2026 games; 0 open paper positions (no verified quote).
* **Kalshi:** 0 trades (optional, not invented).
* **Site:** `index.html` + `app.js` + `data/*.json` served; truth banner: `SOURCE_SNAPSHOT. Only source-backed observations are displayed.`

---

## 17. Limitations / unavailable data

* **Verified facts:** Historical moneyline odds exist (cesar-dx, 94.5% coverage) but lack `observed_at`/`available_at`/`closing_flag` → **EVAL only, no PnL/ROI/CLV/drawdown claim**. Verified quote rows = 0.
* **Unavailable:** Pitch-level Statcast beyond `game_events` aggregates, bullpen workload/leverage details, confirmed lineups/injuries with `available_at`, weather/forecast at decision time, umpire assignment timestamps, rest/travel beyond series_state counters, open/current/close with bid/ask/liquidity/available_size, player props, alternate lines, futures, live, exchange order book. Strategies requiring them are `DATA_UNAVAILABLE`.
* **Postseason small sample:** 440 POST games total; WC 67, WS 66. Managerial hook/pitch-mix/velocity effects not yet measured.
* **Calculation limit:** Pitcher_game aggregates are per-game counts; season-long normalized pitching stats not yet joined.
* **Assumption:** Completed = `home_score NOT NULL` and `status=F`; voids (CR/CA/etc.) never settle.
* **Safety:** No live execution connector; Kalshi treated as market research only.

---

## 18. Remaining work

* Pin per-file SHA256 in `FETCH_MANIFEST` and verify `blobs/{sha}` == local sha after fetch (currently blob_sha recorded, local digest only in `source_observations`).
* Independent cross-source join verification with Retrosheet as second result source (tie-breaker for winner/score).
* Timestamped historical quote ingestion: source must provide `observed_at`/`available_at` and bid/ask/size; otherwise forward-only.
* Starter/lineup availability records with `announced_at`/`available_at`/`confirmed_at` and verification.
* Bullpen workload features (pitches/batters faced last 2 days, high-leverage availability) + Statcast pitch-mix/velocity.
* Walk-forward forward-tests (`TRAIN → VALIDATE → OOS` chronological splits via `chronological_split`) and paper-trade execution recording with slippage/partial fills.
* Round-specific calibration tables and `ALL` roll-up that preserves breakdown.

---

## 19. Next research priorities

1. **Close the price gate:** acquire timestamped historical ML/RL/total quotes (or run 30+ days of live forward collection with observed price + source_observation_ids) to enable verified ROI/CLV.
2. **Hierarchical shrinkage tuning:** cross-validate `weight` schedule and intercept clipping on rolling origin.
3. **Series-state partial pooling:** test `elimination`, `clinch`, `game_number`, `rest_diff` as hierarchical features with shrinkage, not just descriptive win rates.
4. **Starter-adjusted Elo:** ingest probable/confirmed starters and hand-split priors.
5. **Market efficiency by round:** compare opening→close movement where timestamps exist, separately for REG/POST/WC/DS/LCS/WS.
6. **Pitch-mix/velocity persistence:** test whether prior 2-appearance velocity delta adds beyond Elo after controlling for park/weather (when Statcast verified).
7. **Kalshi execution research:** collect bid/ask/size/ fees, measure executable edge vs displayed price, never assume liquidity.

---

## Appendix — Repro commands

```bash
python -m pip install -r requirements.txt
python -m mlbcomp.engine.catalog
python -m mlbcomp.sources                         # registry
python scripts/fetch_sources_github_api.py        # 31 blobs via api.github.com
python -m mlbcomp.ingest.baseballr                # 28072 games, 440 POST, 113 series
python -m mlbcomp.engine.backtest                 # 144098 rows, EL0 Brier 0.259 REG
python -m mlbcomp.engine.research                 # Q01-03, Q09-11, Q13 evaluated; Q19 DATA_UNAVAILABLE
python -m mlbcomp.verify.checks                   # 18 PASS
python -m mlbcomp.web.export_static               # SOURCE_SNAPSHOT JSON
python -m http.server 8000 --bind 0.0.0.0          # preview https://8000-{sandboxId}.e2b.app
python test/engine.test.py && node test/ui.test.js
```

**Provenance chain intact:** `source → retrieval_time → availability_time → derived_value → feature_snapshot_hash → model_output → decision → observed_quote → settlement` with hashes where applicable. Absent link → gate fails, no guess.

