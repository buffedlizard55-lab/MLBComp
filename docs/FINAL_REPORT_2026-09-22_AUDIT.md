# Final Report — 2026-09-22 audit pass — MLBComp

**Branch:** `arena/01a0ca3c-mlbcomp` · **Snapshot as-of:** `2026-09-21` · **Audit date:** `2026-09-22` · **Data mode:** `SOURCE_SNAPSHOT`

This report separates **verified facts**, **calculations**, **assumptions**, and **conclusions**. No real-money bets were placed. Historical wager rows were not rewritten.

This pass is an adversarial audit of the already-published competition, not a new backtest. `data/raw/` and `data/features/` are gitignored; ingest was not re-run.

---

## 1. What was built (and what this pass changed)

The platform already implements the research → verify → model → backtest → forward-test → paper-trade loop with separate `REG`, `POST`, `WC`, `DS`, `LCS`, `WS` environments, a hash-chained ledger, and a GitHub Pages site.

**2026-09-22 audit fixes (verified in this checkout):**

* Analytics tab no longer throws: `#analytics-detail` was missing and aborted `renderAll` before Research / Sources / Verification.
* Ledger and upcoming tables paginate (50 rows) so 20,000 exported records do not freeze the page.
* Strategy drill-down shows exported wagers; filters include game pk, player id, team, season, model, and experiment-alias visibility.
* Research questions Q15–Q18 are wired to Models A–E (previously `NOT_RUN` even though EXP_A–E existed).
* Q06–Q08 are `DATA_UNAVAILABLE` (missing lineups / managerial / Statcast), not silent `NOT_RUN`.
* Q20 records that every strategy is still v1 — no parent comparison to promote.
* Unique POST totals exclude `MLB_POST_MODEL_A`–`E` aliases (double-count fix). Raw totals kept.
* `export_static` refuses to overwrite a SOURCE_SNAPSHOT when `games.parquet` is missing unless `--force`.
* Dedicated postseason Elo (`MLB_POST_DEDICATED_001`, `post_elo`) is catalogued and **not backtested** in this snapshot.
* Chadwick register and Chadwick Retrosheet GitHub mirrors added as NOT_VERIFIED discovery sources (HTTP 200 on 2026-09-22).

---

## 2. Data sources discovered / verified / rejected

**Verified facts (this pass):**

| source | check 2026-09-22 | status |
|---|---|---|
| statsapi.mlb.com | TLS/SSL connection closed | UNAVAILABLE here |
| retrosheet.org | TLS closed | UNAVAILABLE here |
| baseballsavant.mlb.com | TLS closed | UNAVAILABLE here |
| api.weather.gov | TLS closed | UNAVAILABLE here |
| Kalshi trade-api / elections-api | TLS closed | UNAVAILABLE here |
| github.com/chadwickbureau/register | HTTP 200 | NOT_VERIFIED discovery |
| github.com/chadwickbureau/retrosheet | HTTP 200 | NOT_VERIFIED discovery |
| github.com/chadwickbureau/baseballdatabank | HTTP 404 | rejected as a URL |
| sportsdataverse/baseballr-data | already PARTIALLY_VERIFIED in snapshot | unchanged |
| pwu97/bettingtools | already PARTIALLY_VERIFIED | unchanged |
| cesar-dx/mlb-betting-ml | PARTIALLY_VERIFIED, no timestamps | UNVERIFIED for wagers |

No new prices, scores, or fills were ingested.

---

## 3–5. Models

Unchanged runnable models: Elo, form, season, lateform, Poisson totals, bullpen, rest, market move, closing benchmark, transfer (A), round-specific intercept (B), hierarchical (E), series-state (C in EXP_C), round Elo (D).

**New catalog entry, not run:** `MLB_POST_DEDICATED_001` / `post_elo` — REG Elo as prior, then only earlier postseason games update it.

**Audit fact:** this snapshot's `MLB_POST_MODEL_C_001` numbers match hierarchical / MODEL_E, while EXP_C used series-state. Metrics were **not** rewritten.

---

## 6. Strategies

70 versioned hypotheses (69 prior + dedicated Elo). Experiment aliases remain visible and can be hidden on the leaderboard.

---

## 7–9. Backtests / forward tests / paper trading

No new backtest. Published snapshot (as-of 2026-09-21):

* **Verified fact:** `hash_chain_valid: true`, 33,143 immutable ledger rows, 22/22 controls PASS.
* **Verified fact:** 33,143 SETTLED rows with VERIFIED_PRICE; EVAL/PROPOSED carry no PnL.
* **Calculation (unique, aliases excluded):** verified PnL **−$89,575.37**.
* **Published raw total (includes aliases):** −$96,903.05.
* Forward proposals: 623 upcoming predictions for 91 2026 REG games; 0 open paper positions; 0 Kalshi trades.

---

## 10–14. Performance by environment (unique, aliases excluded)

| env | strategies | verified bets | unique verified PnL | status |
|---|---:|---:|---:|---|
| REG | 39 | 31,851 | −78,399.80 | EVALUATED |
| POST | 10 | 485 | −7,024.96 | EVALUATED |
| WC | 4 | 18 | −3,537.48 | EVALUATED |
| DS | 4 | 117 | +3,065.35 | EVALUATED |
| LCS | 4 | 77 | −4,420.41 | EVALUATED |
| WS | 4 | 49 | +741.93 | EVALUATED |

Small postseason priced samples (especially WC n=18) are **INSUFFICIENT_SAMPLE** for an edge claim. DS/WS positive unique PnL is not promoted.

---

## 15. Regular vs postseason model comparison (A–E)

Derived 2026-09-22 from EXP_A–E already in `research_experiments.json` (calculation, not a new backtest):

| id | n | Brier | verified n | verified ROI | vs transfer Brier |
|---|---:|---:|---:|---:|---|
| A transfer | 362 | 0.2521 | 100 | −2.0% | — |
| B adjusted | 362 | 0.2582 | 100 | −23.1% | +0.0061 worse |
| C series-state | 122 | 0.2588 | 38 | +3.0% | +0.0067 worse; ROI n=38 too small |
| D round Elo | 362 | 0.2582 | 100 | −23.6% | +0.0061 worse |
| E hierarchical | 385 | 0.2594 | 123 | +4.0% | +0.0073 worse |

**Conclusion:** INCONCLUSIVE. Transfer has the lowest Brier in this snapshot. No model is promoted. Hierarchical/series-state positive ROI is a small priced sample, not an edge.

---

## 16. Current competition status

* 2026 regular season snapshot: 91 upcoming games, no live Stats API standings (TLS closed 2026-09-22).
* 2026 postseason bracket not loaded; placeholder team names excluded; no postseason wager proposed.
* Site: GitHub Pages from `main` (`https://buffedlizard55-lab.github.io/MLBComp/`). This branch updates it after merge.
* Paper only. No order connector.

---

## 17. Limitations / unavailable data

Pitch-level Statcast, confirmed lineups/injuries with `announced_at`, weather *forecasts* at decision time, umpire assignment timestamps, bid/ask/size, player props, live, exchange, Kalshi, and 2020–2026 timestamped quotes remain unavailable. Players.json is empty. Dedicated Elo is NOT_RUN.

---

## 18. Remaining work

1. Re-run backtest when a source snapshot is present so `post_elo` and remapped MODEL_C get their own rows.
2. Timestamped quotes for 2020–2026 or a live forward-collection window.
3. Cross-source score check vs Chadwick/Retrosheet when reachable.
4. Walk-forward TRAIN/VALIDATE/OOS report exported per strategy.
5. Populate players.json from a verified people register.

---

## 19. Next research priorities

1. Hierarchical vs transfer on a rolling origin (do not promote E from n=123 priced bets).
2. Series-state features with shrinkage after controlling for Elo.
3. Market efficiency by round where verified open/close exists (Q14 already measured mean |move| ≈ 0.025 REG and POST).
4. Starter-adjusted Elo when probable pitchers are PARTIALLY_VERIFIED with a documented first-pitch bound.

---

## Verified facts vs calculations vs assumptions vs conclusions

* **Verified facts:** 22/22 controls in the committed audit JSON; TLS failures on 2026-09-22 for Stats API / Savant / Retrosheet / NWS / Kalshi; GitHub 200 for Chadwick register and Retrosheet mirror; alias rows equal their catalog twins for MODEL_A vs XREG PnL.
* **Calculations:** unique PnL −89,575.37; Q15–Q18 Brier deltas from EXP_A–E.
* **Assumptions:** snapshot as-of remains 2026-09-21; open/close timestamp policy unchanged (first pitch).
* **Conclusions:** no edge; transfer not promoted despite lowest Brier; postseason remains a distinct modeling environment.
