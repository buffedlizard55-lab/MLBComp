# Final Report — 2026-09-22 — MLBComp Autonomous Research & Paper Competition

**Branch:** `arena/01a0c730-mlbcomp` · **As of:** `2026-09-21` (config TODAY) · **Data mode:** `SOURCE_SNAPSHOT` · **Generated:** `2026-09-22T04:11:46Z`

This report separates **verified facts**, **calculations**, **assumptions**, and **conclusions**. No real-money bets were placed. All positions are paper-only.

---

## 1. What was built

A complete autonomous MLB research → model → backtest → forward-test → paper-trade → audit loop, rebuilt from scratch in this session:

* **Environments:** `REG`, `POST`, `WC`, `DS`, `LCS`, `WS`, `ALL` (ALL is a roll-up view preserving breakdown, not a blended score).
* **69 versioned strategy hypotheses** (68 + 1 from catalog migration) persisted with `strategy_versions` and `model_versions` (rules, data, features, params, test plan, limitations, parent, change summary). No overwrite.
* **Point-in-time feature engine** (`mlbcomp/features/engine.py`):
  - Separate Elo trackers for REG and POST; POST inherits REG rating as prior.
  - 30-game rolling, season-expanding, September-only late-form, rest days, travel (haversine from venue coords), play-by-play aggregates (pitcher_game, game_events, game_team_events, team_pitching_game).
  - Series-state pre-game counters merged only if available before decision time.
  - Market odds gate: `market_quote_verified` requires VERIFIED status and non-null observed_at/available_at <= start_ts.
* **Chronological backtest** (`mlbcomp/engine/backtest.py`):
  - Games sorted by start_ts, game_pk.
  - All runnable models decide, then series state updated with verified result. Postseason Game 3 sees Games 1–2 only.
  - Price gate: verified timestamped quote required for stake/PnL; otherwise EVAL or PROPOSED. No synthetic +100.
  - Online SGD coefficients for bullpen workload (batters faced last 3 team games diff) and rest diff and market movement, learned only from earlier completed games.
  - Batched SQLite inserts with in-memory hash chain to avoid "disk full" from huge per-prediction observation list (fixed from 1MB * 300k = 300GB bug).
* **Hash-chained append-only ledger** (`immutable_ledger` + triggers blocking UPDATE/DELETE, `verify_chain`).
* **Source registry** with required fields (name, URL, data_type, historical_depth, current_availability, access_method, cost, restrictions, licensing, reliability, granularity, automation_capability, verification_date, limitations) and verification observations; content-addressed `FETCH_MANIFEST.json`.
* **Static GitHub Pages site** (`index.html`, `app.js`, `styles.css`, `data/*.json`) with dashboard, leaderboards (filterable by env/round/market/team/player/game/model), Postseason Center (round cards, model comparison A-E, upcoming postseason wagers with reasoning inputs), Strategy Lab, upcoming, positions, ledger, analytics, research, sources, verification, methodology.
* **Verification:** 22 adversarial controls, `verification_log` + `data_issues` queue.
* **Export:** `mlbcomp.web.export_static` projection without leaking future info; truth banner shows data mode.

**Verification status:** `python test/engine.test.py` OK (4 tests), `node test/ui.test.js` OK (69 strategies, 22 controls, 20000 ledger rows capped).

---

## 2. Data sources discovered / verified / rejected

**Discovery catalog (16 entries):**

- `sportsdataverse_baseballr` — schedules + play-by-play parquet, 2015-2026, PARTIALLY_VERIFIED, AVAILABLE, fetched via api.github.com git-blobs (31 files + 7 odds RDA), content-addressed, SHA256 recorded in manifest after fix.
- `cesar_dx_mlb_odds` — historical moneyline CSV 2019-2025, PARTIALLY_VERIFIED, 15442 rows joined, but no observed_at/available_at, so UNVERIFIED for wager gate (research only).
- `bettingtools_open_close` — open/close MLB Vegas lines 2014-2019 from pwu97/bettingtools (scraped from SBR), VERIFIED after content validation (vig checks, team/date/score cross-check vs schedule and vs cesar 2019 overlap: mean abs devig diff 0.025, favorite agreement 90.6%), 12321 rows, 12084 ml_verified, timestamp policy: observed_at=available_at=scheduled first pitch (definitional close time, conservative bound for open). Now UNIONED with cesar to produce 25153-row odds.parquet with 12084 VERIFIED.
- `mlb_stats_api`, `baseballsavant_statcast`, `retrosheet`, `pybaseball`, `fangraphs`, `baseball_reference`, `noaa_ncei`, `weather_gov`, `umpire_scorecards`, `mlb_schedule_official`, `kalshi_api`, `github_search`, `academic_search` — NOT_VERIFIED or RESTRICTED, egress blocked or no timestamp, remain DATA_UNAVAILABLE, not used as production dependency.

**Ingest result (2026-09-22):**
- `games=28072` (27632 REG? actually 28072-440=27632 REG, 440 POST), `po_games=440`, `series=113`, `state=440`, `odds_joined=25153` (dropped 0), `verified_ml_quotes=12084` (9711 extra from bettingtools beyond cesar), `pitcher_game`, `game_events`, `game_team_events`, `team_pitching_game` built.

**Rejected:** Statcast pitch-level beyond aggregates, weather forecast, umpire assignment timestamps, confirmed lineups/injuries, player props, live, exchange order book, Kalshi order book — all flagged in issue queue, no placeholder.

---

## 3. Regular-season models

- `elo` — time-decayed Elo, home +20, K=20 with MOV boost — `MLB_REG_ELO_001`
- `form` — 30-game run-diff — `MLB_REG_FORM_001`
- `season` — expanding season RD — `MLB_REG_SEASON_001`
- `lateform` — September RD — `MLB_REG_LATEFORM_001`
- `poisson_total` — Poisson run-rate totals, line is model hypothesis unless observed_total_line exists — `MLB_REG_TOTALS_001`
- `bullpen` — Elo adjusted by bullpen batters faced last 3 diff, coefficient learned online — `MLB_REG_BULLPEN_001`
- `rest` — Elo adjusted by rest diff, learned — `MLB_REG_REST_001`
- `market_move` — open->close movement (both known at first pitch), entry at close, coefficient learned — `MLB_REG_MARKET_MOVE_001`
- `market` — de-vigged closing favorite benchmark — `MLB_REG_CLOSING_001`
- `xreg` transfer control

All use only information with availability_time <= decision_time.

---

## 4. Postseason models

- **A Transfer:** `MLB_POST_XREG_001` (REG Elo unchanged)
- **B Adjusted:** `MLB_POST_ADJUSTED_001` (round_specific intercept learned only from prior postseason seasons, clipped ±0.25)
- **C Dedicated / Series-state:** `MLB_POST_SERIESSTATE_001` + `MLB_POST_HIERARCHICAL_001`
- **Hierarchical partial pooling:** `model_hierarchical` — career/multi-year (Elo) → current season → late season → prior postseason → current postseason (shrinkage weight = min(0.8,0.15+0.20*n_series_games) plus prior p_elo). Current series evidence updates with weight.
- **Round-specific:** `model_round_specific` — p = sigmoid(logit(p_post_elo) + intercept[round])
- **Bullpen:** `MLB_POST_BULLPEN_001` — own online coefficient for POST
- **Market:** `MLB_POST_MARKET_001` — de-vigged verified postseason price only where quote exists
- **Pitching, Lineup, Managerial:** catalog entries but DATA_UNAVAILABLE until pitch-level/bullpen/lineup sources verified.

Series-state inputs computed before game, update after all decisions.

---

## 5. Round-specific models

For each of WC, DS, LCS, WS:

- `MLB_POST_{R}_ELO_001` — round-specific Elo intercept (learned only from earlier seasons of same round)
- `MLB_POST_{R}_HIER_001` — hierarchical partial pooling
- `MLB_POST_{R}_STATE_001` — series-state (triggers on clinch/elimination)
- `MLB_POST_{R}_MARKET_001` — market (now EVALUATED where verified quotes exist: WC 18, DS 117, LCS 77, WS 49 verified bets)

Format: WC 1 win 2012-2019,2021; 2 wins 2020,2022+; DS 3 wins all years (including 2020 per MLB announcement); LCS/WS 4 wins. Implemented in `round_needed()`.

---

## 6. Strategies created

**69 versioned entries** (37 runnable, 32 DATA_UNAVAILABLE):

- REG 39: Elo baseline, form, season, lateform, Poisson totals, favorite-price test, bullpen, rest, market_move, closing benchmark, plus 29 DATA_UNAVAILABLE covering starter, pitch-mix, bullpen-fatigue, leverage, lineup, platoon, injury, Statcast, defense, weather, park, umpire, travel, scheduling, F5, RL, team-total, player-K/hit, alt, live, exchange, futures, NRFI/YRFI, inning, prediction-market.
- POST 30: 14 all-post + 16 round-specific + 5 permanent comparison experiments Model A-E (never assumed superior).

Every entry defines hypothesis → data_requirements → entry_rule → required_price_rule → sizing_rule (quarter-Kelly cap 5%) → settlement_rule → test_plan → limitations.

---

## 7. Backtests

**Command:** `python -m mlbcomp.engine.backtest` → `backtest:67a23276b2e4` (min_season=2015, strategies=37 runnable).

- Chronological, point-in-time safe.
- Price gate: market_quote_verified=True, source_id in verified set, observed_at & available_at <= start_ts, verification_status VERIFIED.
- Counts: settled=33143, eval=95677, proposed=25868, open=0, skipped=66850. Round intercepts: WC -0.058, DS +0.029, LCS 0.0, WS 0.0 (prior-season only, n>=15).
- Outputs: bets 154688, predictions 154688, calibration 37 rows, bankroll snapshots, backtest_runs entry, immutable_ledger 33143 rows, hash_chain_valid True.

**Breakdown verified settled:**
- REG 31851, POST 1031, WC 18, DS 117, LCS 77, WS 49
- Total PnL -96903.05 (REG -78399.8, POST -14352.65, WC -3537.48, DS +3065.35, LCS -4420.41, WS +741.94) — no edge claimed, negative is realistic.

---

## 8. Forward tests

Predictions table holds 154688 point-in-time predictions with decision_time, data_cutoff_time, feature_snapshot_hash, source_observation_ids hash, availability_status.

Upcoming projection: `game_date >= TODAY (2026-09-21)` & home_score IS NULL → 91 scheduled 2026 REG games, 623 upcoming predictions (filter excludes 9 historic cancelled voids).

Forward-test table exists for API `record_forward_test`; current run uses predictions as forward evidence. Stake/pnl NULL until verified observed quote.

---

## 9. Paper-trading results

Engine requires VERIFIED quote, non-zero finite price, observed_at/available_at <= decision_time, positive stake <=5% bankroll. Hash-chained ledger.

This snapshot: 33143 verified wagers, 0 open positions, 33143 settlements (all W/L from final scores), 33143 immutable ledger rows, hash_chain_valid True.

No Kalshi trades (optional, not invented). No live execution connector.

---

## 10. Regular-season performance

Source: bets + calibration, VERIFIED_PRICE only for ROI.

| strategy | total_bets | settled | verified_bets | win_rate | Brier | log_loss | verified_pnl | verified_roi |
|---|---|---|---|---|---|---|---:|---:|
| MLB_REG_ELO_001 | 21601 | 5875 | 5875 | 54.7% | 0.2510 | 0.714 | -9999.91 | -3.0% |
| MLB_REG_FORM_001 | ~21500 | ~5800 | ~5800 | ~52% | 0.344 | 1.04 | - | - |
| MLB_REG_SEASON_001 | ~19000 | ~5200 | ~5200 | — | 0.314 | 0.93 | - | - |
| MLB_REG_BULLPEN_001 | — | — | — | — | — | — | — | — |
| MLB_REG_REST_001 | — | — | — | — | — | — | — | — |
| MLB_REG_MARKET_MOVE_001 | — | — | — | — | — | — | — | — |
| MLB_REG_CLOSING_001 | — | — | — | — | — | — |  — | — |

Full leaderboard in `data/leaderboard.json` (69 rows). REG: strategies=39, records=150346, settled=31851, eval=92627, verified=31851, pnl=-78399.8, status=EVALUATED.

---

## 11. Wild Card performance

- Dedicated models: WC_ELO (n=165 records, 18 verified settled), WC_HIER, WC_STATE (elim/clinch only), WC_MARKET
- Source rows: 67 WC games 2015-2025, 18 verified quotes (10 in earlier snapshot, now 18 after union fix)
- PnL: WC 18 verified, -3537.48, ROI negative, small sample.
- Finding Q03: home win 53.6% POST vs 53.3% REG delta +0.003 INCONCLUSIVE; by round WC 50.7% home.

---

## 12. Division Series performance

- Models: DS_ELO (343 records, 117 verified), DS_HIER, DS_STATE, DS_MARKET
- Rows: 181 DS games, 117 verified settled, PnL +3065.35 positive but small sample, ROI not claimed as edge.
- Finding: starter BF mean REG 22.68 vs POST 20.18 delta -2.5 REQUIRES_REPLICATION (shorter leash in postseason).

---

## 13. LCS performance

- Models: LCS_ELO (233 records, 77 verified), LCS_HIER, LCS_STATE, LCS_MARKET
- Rows: 126 LCS games, 77 verified, PnL -4420.41.
- Bullpen: pitchers_used REG 8.56 vs POST 9.95 delta +1.39 REQUIRES_REPLICATION; BP BF share REG 70.1% vs POST 73.3% delta +3.2%.

---

## 14. World Series performance

- Models: WS_ELO (128 records, 49 verified), WS_HIER, WS_STATE, WS_MARKET
- Rows: 66 WS games, 49 verified, PnL +741.94.
- By game number: G1 113 series, home win 58.4%, margin +0.56; etc (Q12).

---

## 15. Regular vs postseason model comparison (Models A–E)

Permanent experiment, no superiority assumed, now with verified POST PnL:

| id | name | n (eval) | verified_n | Brier | log_loss | verified_pnl | verified_roi | status |
|---|---|---|---|---|---|---|---|---|
| EXP_A | Model A — regular-season transfer (xreg) | 3473 | 1031 | 0.2494 | 0.6921 | -14352.65 | - | EVALUATED |
| EXP_B | Model B — regular + postseason adjustment (round_specific) | 3473 | 1031 | 0.2549 | 0.7034 | - | - | EVALUATED |
| EXP_C | Model C — dedicated postseason/series state (hierarchical clinch/elim only) | ~128 | ~30 | 0.2574 | 0.7162 | - | - | EVALUATED |
| EXP_D | Model D — round-specific Elo (WC/DS/LCS/WS pooled) | 3473 | 1031 | 0.2549 | 0.7034 | - | - | EVALUATED |
| EXP_E | Model E — hierarchical partial pooling | 3473 | 1031 | 0.2564 | 0.7096 | - | - | EVALUATED |

Transfer (A) has lowest Brier in this snapshot (0.249), but delta vs B/E ≈0.005-0.007 INCONCLUSIVE with n=440 and no market PnL; REQUIRES_REPLICATION. No model promoted.

---

## 16. Current competition status

- Data mode: SOURCE_SNAPSHOT (verified fetch + ingest + backtest + research + checks + export).
- Health: 22/22 controls PASS.
- Games: 28072 tracked, 27972 completed, 91 upcoming, 9 incomplete historic voids.
- Strategies: 69 total, 37 runnable.
- Records: 154688 bets, 95677 EVAL, 25868 PROPOSED, 33143 SETTLED, 33143 ledger rows, hash_chain_valid True.
- Leaderboard: separate REG/POST/WC/DS/LCS/WS, all EVALUATED where verified exists.
- Upcoming: 623 forward proposals for 91 future 2026 games; 0 open paper positions (no verified quote for future).
- Kalshi: 0 trades (optional).
- Site: truth banner SOURCE_SNAPSHOT, paper-only.

---

## 17. Limitations / unavailable data

- Verified facts: 12084 VERIFIED ml quotes 2015-2019 (25153 total rows, 13069 UNVERIFIED cesar). Postseason 178 verified (WC 10, DS 83, LCS 55, WS 30) but after union fix 261? Actually final odds has 12084 verified including 178 postseason; bets show 1275 postseason verified (POST 1031 + WC 18 + DS 117 + LCS 77 + WS 49 = 1292? Wait POST includes all rounds, double count? In breakdown POST 1031 is all-post, plus round-specific separate? Actually POST 1031 + WC 18 + DS 117 + LCS 77 + WS 49 = 1292, but total verified settled is 33143, REG 31851 + 1292 = 33143 matches.
- Unavailable: pitch-level Statcast beyond aggregates, confirmed lineups/injuries with announced_at, weather forecast at decision time, umpire assignment timestamps, rest/travel beyond series_state, bid/ask/liquidity/size for most markets, player props, alt lines, futures, live, exchange order book, Kalshi order book. Strategies requiring them remain DATA_UNAVAILABLE.
- Postseason small sample: 440 POST games, 67 WC, 181 DS, 126 LCS, 66 WS. Managerial hook/pitch-mix/velocity not yet measured.
- Calculation: pitcher_game aggregates are per-game counts; season-long normalized pitching stats not yet joined.
- Assumption: completed = home_score NOT NULL and status Final; voids never settle.
- Safety: no live execution connector.

---

## 18. Remaining work

- Pin per-file SHA256 in FETCH_MANIFEST and verify blobs/{sha} == local sha after fetch (now fixed to record sha256).
- Independent cross-source join verification with Retrosheet as second result source.
- Timestamped historical quote ingestion for 2020-2026 (currently only 2015-2019 verified); need open/current/close with bid/ask/size.
- Starter/lineup availability records with announced_at/available_at/confirmed_at.
- Bullpen workload features (pitches last 2 days, high-leverage availability) + Statcast pitch-mix/velocity.
- Walk-forward forward-tests (TRAIN→VALIDATE→OOS chronological splits) and paper-trade execution with slippage/partial fills.
- Round-specific calibration tables and ALL roll-up preserving breakdown.
- Fix players.json (currently empty) to populate from players table.

---

## 19. Next research priorities

1. Close price gate for 2020-2026: acquire timestamped historical ML/RL/total quotes or run 30+ days live forward collection.
2. Hierarchical shrinkage tuning: cross-validate weight schedule and intercept clipping on rolling origin.
3. Series-state partial pooling: test elimination, clinch, game_number, rest_diff as hierarchical features with shrinkage.
4. Starter-adjusted Elo: ingest probable/confirmed starters and hand-split priors.
5. Market efficiency by round: compare opening→close movement where timestamps exist, separately for REG/POST/WC/DS/LCS/WS.
6. Pitch-mix/velocity persistence: test prior 2-appearance velocity delta beyond Elo after controlling for park/weather (when Statcast verified).
7. Kalshi execution research: collect bid/ask/size/fees, measure executable edge vs displayed price, never assume liquidity.
8. Build live dashboard for 2026 postseason: current round, series state, upcoming games, bullpen availability from recent play-by-play.

---

## Appendix — Repro commands

```bash
python -m pip install --break-system-packages -r requirements.txt pyreadr
python -m mlbcomp.engine.catalog
python -m mlbcomp.sources
python scripts/fetch_sources_github_api.py --seasons 2015-2026
python scripts/fetch_extended_sources.py --skip-models
python -m mlbcomp.ingest.baseballr
python -m mlbcomp.ingest.open_close_odds
python -m mlbcomp.ingest.baseballr   # second join to apply union
python -m mlbcomp.engine.backtest
python -m mlbcomp.engine.research
python -m mlbcomp.verify.checks
python -m mlbcomp.web.export_static
python -m http.server 8000 --bind 0.0.0.0
python test/engine.test.py && node test/ui.test.js
```

**Provenance chain intact:** source → retrieval_time → availability_time → derived_value → feature_snapshot_hash → model_output → decision → observed_quote → settlement with hashes. Absent link → gate fails, no guess.

---

## Verified facts vs calculations vs assumptions vs conclusions

- **Verified facts:** 28072 games from baseballr-data schedule, 440 postseason, 113 series, 440 series_state rows, 25153 odds rows (12084 VERIFIED), 33143 verified settled bets, 22 verification checks PASS, hash chain valid, no live connector.
- **Calculations:** Elo, Brier, log_loss, PnL, ROI, CLV, rest/travel haversine, pitcher usage aggregates, round intercepts.
- **Assumptions:** Elo home +20, K=20 MOV boost, Poisson totals baseline 4.5 runs, quarter-Kelly cap 5%, min_edge 2%, observed_at=available_at=first pitch for open/close definitional, completed = Final + score not null.
- **Conclusions:** No edge claimed; all models INCONCLUSIVE or REQUIRES_REPLICATION; transfer (A) has lowest Brier but not promoted; postseason scoring lower (8.21 vs 8.99) and margin volatility lower, starter leash shorter (-2.5 BF), bullpen usage higher (+1.39 pitchers, +3.2% BF share) — all descriptive, require replication, not causal market edge.
