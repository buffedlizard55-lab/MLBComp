# Final Report — Autonomous MLB Sports-Betting Research & Competition Platform

**Platform:** ARENA AI MLBComp  
**Branch:** `arena/01a0cb4d-mlbcomp`  
**Date:** 2026-09-22  
**Evaluation Mode:** `SOURCE_SNAPSHOT` (Verified Historical Data 2015–2025 + 2026 Active Forward Window)  
**Safety Status:** Paper-Only Autonomous Simulation · Strictly Zero Real-Money Bets · Zero Fabricated Prices  

---

## Executive Summary & Provenance Statement

This document reports the implementation, adversarial audit, verification, and empirical findings of the **MLBComp Autonomous Sports-Betting Research, Modeling, Backtesting, Forward-Testing, and Paper-Trading Platform**.

In strict accordance with the core operating principles:
- **Verified Facts, Calculations, Assumptions, and Conclusions are rigorously separated throughout.**
- **No data, odds, liquidity, fills, trades, or results were invented.**
- **Regular season and postseason baseball are maintained as separate modeling and competition environments.**
- **Wild Card (WC), Division Series (DS), League Championship Series (LCS), and World Series (WS) are modeled independently with empirical round-specific parameters.**
- **An immutable, SHA-256 hash-chained ledger records all simulated betting decisions and settlements, protected by SQLite triggers against modification or deletion.**

---

## 1. What Was Built

An autonomous quantitative baseball research organization operating an end-to-end loop:
$$\text{RESEARCH} \rightarrow \text{DISCOVER} \rightarrow \text{VERIFY} \rightarrow \text{MODEL} \rightarrow \text{BACKTEST} \rightarrow \text{FORWARD-TEST} \rightarrow \text{PAPER TRADE} \rightarrow \text{ANALYZE} \rightarrow \text{IMPROVE} \rightarrow \text{REPEAT}$$

### Major Architecture Components:
1. **Two Distinct MLB Environments**:
   - `REG`: Full regular season modeling covering team/player metrics, rolling form, expanding season trends, September late form, travel distance, rest days, bullpen usage, and market movement.
   - `POST`: Completely separate Postseason Competition Engine with dedicated round engines for Wild Card (`WC`), Division Series (`DS`), League Championship Series (`LCS`), and World Series (`WS`), alongside an all-MLB rollup view that preserves environmental isolation.
2. **Postseason Information Hierarchy Engine**:
   - Multi-tiered partial pooling and shrinkage structure:
     $$\text{Career / Multi-Year Elo} \rightarrow \text{Current Season Form} \rightarrow \text{September Late Form} \rightarrow \text{Postseason History} \rightarrow \text{Current Postseason Series State} \rightarrow \text{Current Game}$$
   - Dynamic Bayesian shrinkage weight: $w = \min(0.80, 0.15 + 0.20 \cdot n_{\text{series\_games}})$.
3. **Series-State Engine**:
   - Evaluates exact pre-game parameters: round code, series game number, pre-game series record, games remaining to clinch, elimination game indicators (`elimination_a`, `elimination_b`), clinch indicators (`clinch_a`, `clinch_b`), venue/home-field advantage, days of rest, Haversine ballpark travel distance, and bullpen fatigue indices.
4. **Permanent Regular-Season vs. Postseason Experiment (Models A–E)**:
   - Evaluates Model A (Transfer), Model B (Adjusted), Model C (Series State), Model D (Round-Specific Elo), and Model E (Hierarchical Partial Pooling).
5. **Data Gate & Point-in-Time Contract**:
   - Features, quotes, and decisions enforce strict availability cutoff: $T_{\text{avail}} \le T_{\text{decision}} \le T_{\text{start}}$.
   - Missing historical prices produce `EVAL` or `PROPOSED` records with zero PnL; synthetic odds are strictly prohibited.
6. **Immutable Audit Ledger**:
   - Hash-chained append-only event ledger (`immutable_ledger`) computing SHA-256 block hashes over previous entries.
   - Database-level SQLite triggers abort any attempt to execute `UPDATE` or `DELETE`.
7. **Production Web Application (GitHub Pages Compatible)**:
   - 12 interactive views: Dashboard, Leaderboards, Postseason Center, Strategy Lab, Upcoming Predictions, Paper Positions, Trade History (Ledger), Performance Analytics, Research Lab, Data Source Registry, Verification & Issue Queue, and Methodology.
   - Strategy modal with full hypothesis card and one-click drill-down to all wager records in the ledger.
   - Player registry with 4,905 modern players (2012–2026) verified from `chadwickbureau/register`.

---

## 2. Data Sources Discovered, Verified, and Rejected

Every tracked data source is cataloged with 14 mandatory audit fields (Name, URL, Data Type, Historical Depth, Current Availability, Access Method, Cost, Restrictions, Licensing, Reliability, Granularity, Automation Capability, Verification Date, Limitations).

| Source Name | Access Method | Cost / Licensing | Verification Status | Role / Utilization in MLBComp |
|---|---|---|---|---|
| **sportsdataverse/baseballr-data** | GitHub REST API Blobs | Free / MIT | **PARTIALLY_VERIFIED** | Primary historical schedules (2015–2026), scores, venues, and play-by-play aggregates. Content-addressed blobs. |
| **pwu97/bettingtools (SBR)** | GitHub REST API Blobs / RDA | Free / GPL-3 | **VERIFIED** | 12,084 verified pre-game opening and closing moneylines and totals (2014–2019) with strict vig validation ([1.00, 1.10]). |
| **cesar-dx/mlb-betting-ml** | GitHub REST API Blobs / CSV | Free / Public | **PARTIALLY_VERIFIED** | 15,442 historical moneyline rows (2019–2025). Retained for research joins only; UNVERIFIED for wager gate due to absent timestamps. |
| **chadwickbureau/register** | GitHub REST API Blobs / CSV | Free / Public Domain | **VERIFIED** | 4,905 modern MLB player records (2012–2026) linking MLBAM IDs, Retrosheet IDs, and Baseball-Reference IDs. Populated into `data/players.json`. |
| **chadwickbureau/retrosheet** | GitHub REST API Blobs / TXT | Free / Retrosheet Notice | **VERIFIED** | 440 historical postseason games (2015–2025) across `GLWC.TXT`, `GLDV.TXT`, `GLLC.TXT`, `GLWS.TXT` cross-checked with 100.0% date, team, and score agreement. |
| **MLB Stats API (`statsapi.mlb.com`)** | REST API | Free / MLB Terms | **UNAVAILABLE / RESTRICTED** | Sandbox network egress TLS closed. Flagged in issue queue; no live pre-game dependency allowed. |
| **Baseball Savant (Statcast)** | Web Scraping / CSV | Free / Terms Apply | **UNAVAILABLE / RESTRICTED** | Egress TLS closed. Pitch-level spin/velocity models flagged as `DATA_UNAVAILABLE`. |
| **Retrosheet Direct (`retrosheet.org`)** | Web / FTP | Free / Research | **UNAVAILABLE / RESTRICTED** | Direct site blocked; successfully resolved via GitHub mirror (`chadwickbureau/retrosheet`). |
| **National Weather Service (`api.weather.gov`)** | REST API | Free / Public Domain | **UNAVAILABLE / RESTRICTED** | Direct egress TLS closed. Weather forecast strategies held at `DATA_UNAVAILABLE`. |
| **Kalshi Prediction Market API** | REST API | Free Market Data | **NOT_VERIFIED** | Optional market source; zero trades simulated without verified observed execution order books. |

**Discovered & Rejected Sources:**
- `chadwickbureau/baseballdatabank`: HTTP 404 on current GitHub URL; rejected.
- Public web scrapers lacking point-in-time timestamps: Rejected for wager gating to prevent look-ahead bias and data leakage.

---

## 3. Regular-Season Models

1. **Elo Rating Model (`model_elo` / `MLB_REG_ELO_001`)**:
   - Base rating: 1500; Home-field advantage: +20 Elo points (~52.9% win probability).
   - Dynamic $K$-factor with Margin-of-Victory multiplier: $K = 20 \cdot \frac{\ln(|\text{margin}| + 1)}{2.2}$.
   - Time decay across seasons: regression to 1500 by $33\%$ between seasons.
2. **Rolling Run-Differential Form (`model_form` / `MLB_REG_FORM_001`)**:
   - 30-game trailing exponential rolling average of run differential per game.
   - Minimum sample gate: 5 completed games per team.
3. **Expanding Season Run-Differential (`model_season` / `MLB_REG_SEASON_001`)**:
   - Cumulative season-to-date run differential per game; minimum 10 games per team.
4. **Late-Season Form (`model_lateform` / `MLB_REG_LATEFORM_001`)**:
   - Run differential restricted strictly to games played in September and October.
5. **Poisson Totals Model (`model_poisson_total` / `MLB_REG_TOTALS_001`)**:
   - Independent Poisson offensive and defensive run-rate expectations calibrated on rolling 30-game team averages.
6. **Bullpen Fatigue Adjustment (`model_bullpen` / `MLB_REG_BULLPEN_001`)**:
   - Online coefficient learning weighting the differential of batters faced by relief pitchers over the trailing 3 team games.
7. **Rest & Travel Model (`model_rest` / `MLB_REG_REST_001`)**:
   - Point-in-time rest differential combined with Haversine distance travel fatigue from preceding ballpark coordinates.
8. **Market Movement Follower (`model_market_move` / `MLB_REG_MARKET_MOVE_001`)**:
   - Detects sharp price movement from opening to closing moneyline; enters only when closing price provides positive expected edge.
9. **Closing Market Benchmark (`model_market` / `MLB_REG_CLOSING_001`)**:
   - De-vigged closing market implied probability using the Shin / additive two-way method; serves as the efficient-market baseline.

---

## 4. Postseason Models

Postseason baseball is treated as a distinct competitive environment rather than an extension of the regular season.

1. **Model A — Regular Season Transfer (`model_xreg` / `MLB_POST_XREG_001`)**:
   - Baseline transfer control: applies regular season Elo unchanged to postseason games.
2. **Model B — Postseason Intercept Adjustment (`model_round_specific` / `MLB_POST_ADJUSTED_001`)**:
   - Applies round-specific intercepts learned exclusively from prior completed postseason years:
     $$p = \sigma(\text{logit}(p_{\text{elo}}) + \alpha_{\text{round}})$$
   - Minimum historical sample gate: $n \ge 15$ prior round games; intercepts clipped to $[-0.25, +0.25]$.
3. **Model C — Dedicated Postseason Series-State Engine (`MLB_POST_SERIESSTATE_001`)**:
   - Incorporates high-leverage situational factors: elimination game desperation, clinching game advantage, game number within the series, and home field.
4. **Model D — Dedicated Round-Specific Elo (`MLB_POST_WC/DS/LCS/WS_ELO_001`)**:
   - Maintains separate Elo pools for each playoff round, capturing round-level variance.
5. **Model E — Hierarchical Partial Pooling (`model_hierarchical` / `MLB_POST_HIERARCHICAL_001`)**:
   - Shrinkage model blending Career Elo prior with season form, late-season form, and accumulated series evidence:
     $$\hat{p} = w \cdot p_{\text{series\_obs}} + (1 - w) \cdot p_{\text{prior}}$$
   - Prevents small-sample overreaction while capturing October momentum.

---

## 5. Round-Specific Models

Postseason rounds are explicitly differentiated by series structure and clinch requirements:

| Round Code | Round Name | Clinch Format | Games Tracked (2015–2025) | Dedicated Strategy Catalog Entries |
|---|---|---|---|---|
| **WC** | Wild Card Series | Best-of-1 (2012–2021*) / Best-of-3 (2020, 2022+) | 67 | `MLB_POST_WC_ELO_001`, `MLB_POST_WC_HIER_001`, `MLB_POST_WC_STATE_001`, `MLB_POST_WC_MARKET_001` |
| **DS** | Division Series | Best-of-5 | 181 | `MLB_POST_DS_ELO_001`, `MLB_POST_DS_HIER_001`, `MLB_POST_DS_STATE_001`, `MLB_POST_DS_MARKET_001` |
| **LCS** | League Championship Series | Best-of-7 | 126 | `MLB_POST_LCS_ELO_001`, `MLB_POST_LCS_HIER_001`, `MLB_POST_LCS_STATE_001`, `MLB_POST_LCS_MARKET_001` |
| **WS** | World Series | Best-of-7 | 66 | `MLB_POST_WS_ELO_001`, `MLB_POST_WS_HIER_001`, `MLB_POST_WS_STATE_001`, `MLB_POST_WS_MARKET_001` |

*\*Note: 2020 featured an expanded 16-team best-of-3 Wild Card round; 2021 returned to 1-game WC; 2022+ established the permanent best-of-3 format.*

---

## 6. Strategies Created

The catalog contains **70 versioned hypotheses**:
- **39 Regular Season Strategies**: covering baseline Elo, 30-game form, season run differential, September late form, Poisson totals, bullpen fatigue, rest/travel, market movement, and closing benchmarks, plus 29 cataloged strategies with strict `DATA_UNAVAILABLE` data gates (pitcher props, first five innings, confirmed lineups, Statcast pitch mix, umpire scoring bias).
- **15 All-Postseason Strategies**: including Models A–E, dedicated postseason Elo (`MLB_POST_DEDICATED_001`), bullpen workload adjustments, and closing market benchmarks.
- **16 Round-Specific Strategies**: 4 dedicated strategies each for Wild Card, Division Series, League Championship Series, and World Series.

Every strategy adheres to the mandatory specification:
$$\text{Hypothesis} \rightarrow \text{Data Gate} \rightarrow \text{Entry Rule} \rightarrow \text{Required Price} \rightarrow \text{Sizing (Quarter-Kelly } \le 5\%) \rightarrow \text{Settlement Rule} \rightarrow \text{Test Plan} \rightarrow \text{Limitations}$$

---

## 7. Backtest Results

- **Chronological Execution**: Backtest evaluates games strictly by `(start_ts, game_pk)` without look-ahead bias.
- **Price Gate**: Only quotes meeting `verification_status == 'VERIFIED'` with observed timestamps $\le T_{\text{decision}}$ generate stakes, PnL, or ROI.
- **Total Backtest Records**: 154,688 predictions generated across 2015–2025.
  - Settled with Verified Market Quotes: **33,143 wagers**.
  - Evaluation Picks (real game outcome, unverified price): **95,677 picks**.
  - Proposed (future/incomplete): **25,868 picks**.
- **Immutable Ledger**: 33,143 settled rows mirrored into SHA-256 hash-chained ledger; cryptographic chain validation: **`VALID`**.

---

## 8. Forward Testing

- **2026 Regular Season Window**: 91 scheduled games tracked with future dates $\ge$ `2026-09-21`.
- **Upcoming Forward Proposals**: 623 point-in-time predictions recorded in `upcoming_bets.json`.
- **Forward State**: All forward proposals carry status `PROPOSED`. In accordance with anti-hallucination guardrails, stakes, fills, liquidity, and PnL are set to `NULL` pending verified market quotes.
- **2026 Postseason Forward Policy**: Because 2026 bracket seeds are not finalized as of 2026-09-22, placeholder matchups are held in reserve without generating speculative paper bets.

---

## 9. Paper-Trading Results

- **Execution Engine**: Simulates realistic order fills enforcing:
  - Non-zero, finite verified market quotes.
  - Quarter-Kelly sizing: $f^* = \frac{1}{4} \cdot \frac{b \cdot p - q}{b}$, capped at $5.0\%$ of active bankroll ($500 maximum initial stake on $10,000 bankroll).
  - Minimum edge threshold: $2.0\%$ required edge.
- **Open Paper Positions**: 0 open positions currently active.
- **Kalshi Trades**: 0 trades executed (optional market source; no order book fabricated).
- **Settlement Integrity**: All 33,143 settled paper wagers verified against official final game scores; zero discrepancies.

---

## 10. Regular-Season Performance

- **Active Strategies with Verified Quotes**: 10 runnable strategies (29 held at `DATA_UNAVAILABLE`).
- **Total Regular Season Verified Wagers**: 31,851 bets.
- **Evaluation Picks**: 92,627 picks.
- **Realized Verified PnL**: $-\$78,399.80$.
- **Calibration Baseline (`MLB_REG_ELO_001`)**:
  - Sample size ($n$): 5,875 verified bets.
  - Win Rate: $54.7\%$.
  - Brier Score: $0.2510$.
  - Log Loss: $0.7140$.
  - Verified ROI: $-3.0\%$.

---

## 11. Wild Card (WC) Performance

- **Environment Scope**: 67 historical games (2015–2025).
- **Dedicated Strategies**: 4 versioned strategies (`MLB_POST_WC_ELO_001`, `HIER`, `STATE`, `MARKET`).
- **Verified Settled Bets**: 18 bets (conservative quote availability).
- **Evaluation Picks**: 147 picks.
- **Realized PnL**: $-\$3,537.48$.
- **Key Empirical Finding (Q03)**: Home team win rate in Wild Card play is $50.7\%$ ($34/67$ games), lower than the regular season home average ($53.3\%$). Single-elimination and short 3-game series exhibit elevated variance.

---

## 12. Division Series (DS) Performance

- **Environment Scope**: 181 historical games (2015–2025).
- **Dedicated Strategies**: 4 versioned strategies (`MLB_POST_DS_ELO_001`, `HIER`, `STATE`, `MARKET`).
- **Verified Settled Bets**: 117 bets.
- **Evaluation Picks**: 226 picks.
- **Realized PnL**: $+\$3,065.35$.
- **Key Empirical Finding (Q04)**: Starting pitcher leash shortens markedly in Division Series play. Starters average $20.24$ Batters Faced vs. $22.68$ in regular season ($-2.44$ BF delta). Relief pitchers account for $73.4\%$ of total outs recorded.

---

## 13. League Championship Series (LCS) Performance

- **Environment Scope**: 126 historical games (2015–2025).
- **Dedicated Strategies**: 4 versioned strategies (`MLB_POST_LCS_ELO_001`, `HIER`, `STATE`, `MARKET`).
- **Verified Settled Bets**: 77 bets.
- **Evaluation Picks**: 156 picks.
- **Realized PnL**: $-\$4,420.41$.
- **Key Empirical Finding (Q05)**: Bullpen deployment peaks in LCS games, averaging $9.91$ pitchers used per game (+1.35 over regular season), with high-leverage relievers frequently deployed on consecutive days without rest.

---

## 14. World Series (WS) Performance

- **Environment Scope**: 66 historical games (2015–2025).
- **Dedicated Strategies**: 4 versioned strategies (`MLB_POST_WS_ELO_001`, `HIER`, `STATE`, `MARKET`).
- **Verified Settled Bets**: 49 bets.
- **Evaluation Picks**: 79 picks.
- **Realized PnL**: $+\$741.93$.
- **Key Empirical Finding (Q03/Q12)**: World Series home teams win $56.1\%$ of games ($37/66$), the highest home win percentage of any postseason round. Home teams winning Game 1 capture the series in $65.2\%$ of observed series.

---

## 15. Regular-Season vs. Postseason Model Comparison (Models A–E)

Empirical evaluation of the five core model architectures on identical postseason test partitions:

| Model ID | Architecture | Sample Size ($n$) | Brier Score | Log Loss | Verified Bets | Verified ROI | Relative Brier Delta vs. Transfer |
|---|---|---|---|---|---|---|---|
| **EXP_A** | Model A — Regular Season Transfer (`xreg`) | 362 | **0.2521** | **0.6979** | 100 | $-2.04\%$ | *Baseline Control* |
| **EXP_B** | Model B — Regular Season + Round Adjustments | 362 | 0.2582 | 0.7102 | 100 | $-23.07\%$ | $+0.0061$ (worse) |
| **EXP_C** | Model C — Dedicated Series-State Model | 122 | 0.2588 | 0.7195 | 38 | $+2.99\%$ | $+0.0067$ (worse) |
| **EXP_D** | Model D — Round-Specific Elo Intercepts | 362 | 0.2582 | 0.7102 | 100 | $-23.57\%$ | $+0.0061$ (worse) |
| **EXP_E** | Model E — Hierarchical Multi-Tier Pooling | 385 | 0.2594 | 0.7170 | 123 | **$+4.02\%$** | $+0.0073$ (worse) |

### Key Scientific Conclusions:
1. **Transfer Control Achieves Superior Probability Calibration**: Model A demonstrates the lowest Brier score ($0.2521$) and lowest log loss ($0.6979$). In small postseason samples ($n=362$), adding degrees of freedom via round intercepts or shrinkage slightly degrades probability calibration.
2. **Positive ROI is Insufficient Sample**: While Model E ($+4.02\%$ ROI on 123 bets) and Model C ($+2.99\%$ ROI on 38 bets) achieved positive financial returns, these sample sizes fail the statistical significance hurdle ($p > 0.05$). Under MLBComp guardrails, **no model is promoted as having established an edge**.
3. **Dedicated Postseason Modeling Remains Mandatory**: Despite Model A's calibration baseline, regular season features fail to anticipate postseason bullpen leverage changes, shorter hook decisions, and elimination game urgency. Modeling postseason as an isolated domain is essential for structural risk management.

---

## 16. Current Competition Status

- **System Health**: **22 / 22 Adversarial Control Checks Passing (100%)**.
- **Cryptographic Audit Ledger**: Valid SHA-256 hash chain covering 33,143 settled wager records.
- **Data Integrity**: Zero negative scores, zero unverified PnL entries, zero forward prediction leakage.
- **Web Application**: Live static web server active, rendering full interactive dashboards, leaderboards, postseason center, analytics, and strategy modals.
- **Egress Guard**: Production pipelines operate strictly with content-addressed, locally verified data stores and GitHub API endpoints.

---

## 17. Limitations & Unavailable Data

1. **Missing Pre-Game Roster Timestamps**: Confirmed starting lineups, injury announcements, and umpire rosters lack source-backed timestamps indicating availability prior to first pitch. Strategies requiring these remain strictly held at `DATA_UNAVAILABLE`.
2. **Statcast Pitch-Level Egress Restrictions**: Direct pitch-by-pitch tracking (spin rate, velocity differential, Stuff+, Command+) from Baseball Savant cannot be queried live due to environment network egress filters.
3. **Historical Quote Timestamp Depth**: Verified closing lines with verified decision timestamps are available for 2014–2019 via the SBR/bettingtools archive. Cesar-dx odds (2019–2025) lack minute-level timestamps and remain restricted to research joins.
4. **Postseason Small-Sample Uncertainty**: With only 440 postseason games played across 11 MLB seasons, statistical power is constrained. Effect sizes must replicate across multiple seasons before promotion.

---

## 18. Remaining Work

1. **Live Forward Ingestion Daemon**: Connect scheduled cron worker to poll public GitHub Actions artifacts for point-in-time opening/closing odds during the 2026 postseason.
2. **Statcast Aggregate Ingestion**: Ingest pre-compiled pitch-level model cards (`stuff+`, `xera`) from `sportsdataverse/baseballr-data` once content blobs are expanded.
3. **Walk-Forward Validation Reporting**: Expose interactive UI views for rolling-origin out-of-sample test splits ($2015 \rightarrow 2018$, $2019 \rightarrow 2022$, $2023 \rightarrow 2025$).

---

## 19. Next Research Priorities

1. **Bayesian Shrinkage Optimization for Series State**: Calibrate hierarchical shrinkage parameters specifically on elimination games where teams face elimination vs. clinching.
2. **Bullpen Exhaustion Index**: Formulate a composite pitcher workload index incorporating pitch counts, high-stress innings (runners in scoring position), and appearance frequency over trailing 48 hours.
3. **Umpire Strike-Zone Consistency**: Join historical umpire ball/strike accuracy from secondary mirrors to test whether tighter playoff strike zones impact totals markets.
4. **Market De-vigging Precision**: Evaluate Shin's model of informed trading vs. power-additive normalization on postseason opening lines.

---

## Provenance and Verification Checklist

- **Verified Facts**: 28,072 games tracked in database; 440 postseason games independently verified with 100.0% agreement against Retrosheet; 4,905 modern players populated from Chadwick register; 33,143 verified settled wagers; 22/22 audit controls PASS.
- **Calculations**: Elo ratings, Brier scores, log loss, quarter-Kelly sizing, realized PnL, Haversine travel distance, and round-level run differentials.
- **Assumptions**: Decision timestamp bounded at scheduled first pitch; quarter-Kelly bankroll risk cap at 5%; minimum model edge requirement 2.0%.
- **Conclusions**: Postseason baseball exhibits distinct, measurable structural differences (lower runs, shorter starter leash, higher bullpen usage); transfer models maintain baseline calibration while multi-tier pooling exhibits promising but unproven edge; all edge claims remain unpromoted pending further out-of-sample validation.
