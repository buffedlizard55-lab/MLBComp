# ARENA — MLB Autonomous Sports-Betting Research & Testing System

**Paper-trading research and model competition only. This system never places,
supports, or simulates real-money wagering.** It discovers, tests, ranks and
rejects MLB betting strategies against *verified* data, with the regular
season and the postseason run as **separate competitions**.

Last audited **2026-09-21** — see [`docs/AUDIT_2026-09-21.md`](docs/AUDIT_2026-09-21.md).
That audit withdrew the previous README's headline numbers and corrected the
data pipeline; read it before citing anything here.

---

## Rebuild from source (all commands verified this session)

```bash
python3 -m pip install -r requirements.txt        # pandas, numpy, pyarrow, flask
export GH_TOKEN=$(gh auth token)                  # api.github.com rate limits
python3 scripts/fetch_sources_github_api.py       # 31 files + FETCH_MANIFEST.json
python3 -m mlbcomp.ingest.baseballr               # 28,072 games, 15,442 odds rows (~60s)
python3 -m mlbcomp.features.po_corpus             # 440 PO games, champion cross-check
python3 -m mlbcomp.engine.backtest                # the competition
python3 -m mlbcomp.engine.research                # 20 questions + Models A-E
python3 -m mlbcomp.verify.checks                  # 16 checks
python3 -m mlbcomp.web.export_static              # writes data/*.json for the site
python3 -m http.server 8000 --bind 0.0.0.0        # GitHub Pages site
```

`data/raw/`, `data/features/` and `data/mlbcomp.db` are git-ignored; only
`data/*.json` (the published site payload) is committed.

## Data & provenance

| tier | data | verified how | used for |
|---|---|---|---|
| **S1** `sportsdataverse/baseballr-data` | 28,072 games 2015-2026, play-by-play, 440 postseason games | REG 2019-2025 cross-checked against S2 (15,442 rows, 100% outcome agreement); **postseason cross-checked against the documented WS champions for all 11 seasons** | everything |
| **S2** `cesar-dx/mlb-betting-ml` | real moneylines 2019-2025 regular season | date+team join with 0 mismatches | the only real market prices in the system |
| **rejected** | `statsapi.mlb.com`, `baseballsavant.com`, `api.elections.kalshi.com`, `raw.githubusercontent.com` | unreachable from this environment (recorded, not estimated) | weather / Statcast / Kalshi / live APIs are `DATA_UNAVAILABLE` |

Fetches go through `api.github.com` git-blobs because `raw.githubusercontent.com`
is blocked here; every file's blob SHA is in `data/raw/FETCH_MANIFEST.json`.

**No verified market prices exist for any postseason game** in any reachable
source. Postseason results are therefore reported as **model-skill win rates on
real outcomes**, never as ROI.

### Correction to the previous version of this README

The previous README stated that the S1 mirror's 2016-2024 postseason was
"partially fabricated" and used a 256-game corpus of hand-reconstructed
winners instead. **Both claims were wrong.** The mirror's postseason is
authentic; the "fabrication" was detected by a `KNOWN_WS_CHAMPIONS` table that
was wrong in 5 of 11 seasons (2016, 2020, 2021, 2023, 2024), and the
replacement corpus itself asserted invented series results (Rays over Dodgers
2020, Astros over Braves 2021, Dodgers sweeping the Rangers in 2023, Yankees
over Dodgers 2024, Cardinals over the Nationals in the 2019 NLCS). Every
postseason figure derived from it is withdrawn. Details and evidence:
[`docs/AUDIT_2026-09-21.md`](docs/AUDIT_2026-09-21.md).

## Results (rebuilt 2026-09-21)

**Regular season, settled at verified 2019-2025 moneylines** — ROI per dollar
staked. No strategy beats the market.

| strategy | verified bets | ROI | win% |
|---|---|---|---|
| MLB_REG_SEASON_001 | 12,553 | −0.262% | 56.73 |
| MLB_REG_FORM_001 | 13,518 | −0.294% | 56.00 |
| MLB_REG_ELO_001 | 10,074 | −0.382% | 55.48 |
| MLB_REG_LATEFORM_001 | 13,731 | −0.645% | 54.40 |
| MLB_REG_FAV_BIAS_001 | 6,246 | −2.457% | 63.50 |

`MLB_REG_TOTALS_001` (25,520 bets) settles against a **synthetic** 8.5 line —
no verified totals odds exist — and is excluded from every market claim.

**Postseason, model-skill win rate on real outcomes** (not ROI):

| round | strategy | picks | win% | Brier |
|---|---|---|---|---|
| WC | MLB_POST_WC_ELO_001 | 48 | 58.33 | 0.2410 |
| DS | MLB_POST_DS_ELO_001 | 130 | 54.62 | 0.2451 |
| LCS | MLB_POST_LCS_ELO_001 | 83 | 49.40 | 0.2527 |
| WS | MLB_POST_WS_ELO_001 | 44 | 52.27 | 0.2531 |
| POST | MLB_POST_HIERARCHICAL_001 | 352 | 56.53 | 0.2614 |
| POST | MLB_POST_XREG_001 (regular-season model transferred) | 295 | 53.90 | 0.2482 |
| POST | MLB_POST_SERIESSTATE_001 | 83 | 54.22 | 0.2482 |
| POST | MLB_POST_LATESEASON_001 | 404 | 53.22 | 0.3248 |

These samples are 44-404 picks. They are **not** edge claims, and round-to-round
differences at this size are not significant. The Models A-E framework
(regular-season transfer vs adjusted vs dedicated vs round-specific vs
hierarchical) is permanent and re-runs each season.

Verification: **16/16 checks pass**, including the mirror's World Series
champions for all 11 seasons, best-of-N series lengths, no play after clinch,
odds join and outcome agreement. Unlike the previous version, these checks
**fail** on mismatch instead of recording a quarantine and passing.

## Repository layout

```
scripts/fetch_sources_github_api.py  real source fetcher (api.github.com blobs)
mlbcomp/
  config.py            paths, environments, odds math, research questions
  db.py                SQLite schema + audit helpers
  ingest/baseballr.py  sources, canonical ids, dedup, series engine, odds join
  features/engine.py   point-in-time features (Elo, form, rates, rest)
  features/po_corpus.py  postseason corpus + champion cross-check (hard gate)
  engine/strategies.py models + 14-strategy catalog (REG x6, POST x8)
  engine/backtest.py   walk-forward competition, market gate, stats
  engine/research.py   20 questions, findings, experiments A-E
  verify/checks.py     16 verification checks
  web/app.py           Flask view over the database
  web/export_static.py the ONLY writer of data/*.json (from the database)
data/                  *.json site payload (committed); raw/, features/, db ignored
scripts/RETIRED_generate_mlbcomp_data.py   the fabricated generator — DO NOT RUN
```

## Anti-leakage contract

* games processed strictly in start-time order; team state updates only after
  a game completes;
* round intercepts (Model D) learned only from **prior** postseason seasons and
  promoted at the season boundary;
* series state uses only earlier games of the same series; rest/travel computed
  point-in-time;
* games without scores are never settled (e.g. the rain-suspended 2022 WS
  Game 3);
* uncompleted 2026 games produce PROPOSED picks, never settlements.

## Limitations (disclosed, per spec)

1. **No postseason market prices exist** → postseason ROI cannot be measured;
   only model-skill metrics are reported.
2. **No weather, lineups, umpires or probable-starter identities** → those
   hypothesis families are `DATA_UNAVAILABLE`, not estimated.
3. **Kalshi is unreachable** → `kalshi_trades.json` is an empty list. No fills,
   quotes or liquidity are simulated.
4. **2026 is a single-source in-progress snapshot** (through 2026-09-19), and
   unpriced.
5. The engine writes an assumed +100 placeholder for unpriced games; those rows
   are labeled `ASSUMED_PRICE_PLUS100` and never counted as market results.
6. Compounded quarter-Kelly bankroll is an artefact and is not published; ROI
   per dollar staked is the metric.
7. Postseason samples are small; no edge is claimed from them.
