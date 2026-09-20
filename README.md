# ARENA — MLB Autonomous Sports-Betting Research & Testing System

**Paper-trading research and model competition only. This system never places,
supports, or simulates real-money wagering.** It discovers, tests, ranks and
rejects MLB betting strategies against *verified* data, with the regular
season and the postseason run as **separate competitions**.

Built and audited 2026-09-20. Python 3.11, pandas, SQLite, Flask.

---

## What it does

| Layer | What |
|---|---|
| **Data** | 28,072 games 2015-2026 (mirror), 15,442 verified real moneylines 2019-25, verified postseason corpus (256 games 2019-25), play-by-play events 2015-26 |
| **Environments** | REG (own bankrolls/leaderboard) · POST OVERALL · WC · DS · LCS · WS (each its own competition, models, backtests, leaderboards) · ALL-MLB computed view that never obscures the split |
| **Engine** | Point-in-time feature engine (Elo, 30-game form, September form, season-expanding stats, K/BB/HR rates, rest, series state) — strict anti-leakage |
| **Competition** | 14 strategies (6 REG + 8 POST), walk-forward backtest 2015→2026, per-strategy×env paper bankrolls (Kelly vs real prices where they exist), Brier/log-loss calibration, drawdown |
| **Research** | 20 empirical questions with verdicts, permanent Models A-E experiment framework, 2026 live forward-testing ledger (532 tracked picks) |
| **Website** | Dashboard, Leaderboards, **Postseason Center**, **Upcoming Postseason Strategy Bets**, Research, Verification, Audit (Flask, port 8000) |
| **Integrity** | 16/16 verification checks, source registry with rejected sources, fabricated-data quarantine with documented detection, full audit trail |

## Quick start

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# data must be present under data/ (see "Data" below)
.venv/bin/python -m mlbcomp.ingest.baseballr   # build DB + features (~90s)
.venv/bin/python -m mlbcomp.verify.checks      # 16 verification checks
.venv/bin/python -m mlbcomp.engine.backtest    # run the competition
.venv/bin/python -m mlbcomp.engine.research    # findings + experiments A-E
.venv/bin/python -m mlbcomp.web.app            # website on :8000
```

## Data & provenance (read this first)

The system's core finding about its own inputs: **the primary mirror
(sportsdataverse/baseballr-data) has a partially fabricated 2016-2024
postseason** — e.g. a "2016 World Series" Cubs-Indians game dated 2016-10-25
that never occurred, and World Series results flipped in 2020 (TB actually
beat LAD 4-2), 2021 and 2024. Detection is automated (champion cross-check +
series-format/clinch checks) and recorded in `verification_log`.

Consequently the system uses a **tiered, labeled provenance model**:

| Tier | Data | Used for |
|---|---|---|
| **REG 2019-2025** | mirror + cesar-dx moneylines; 15,442/15,442 date+team matches, 100% outcome agreement | real-price ROI (the only real market in this system) |
| **REG 2015-2018, 2026** | mirror only (single-source; 2026 live through 2026-09-19) | model training/calibration; 2026 picks tracked as PROPOSED (no prices yet) |
| **P1 — PO 2025** (47 games) | mirror, reality spot-checked (every series matches the documented 2025 postseason, incl. LAD 4-3 TOR in 7) | ML **and** totals (real scores) |
| **P2 — PO 2019-2024** (209 games) | game **winners** reconstructed from documented public results (`mlbcomp/data_recon/po_results.py`, per-series confidence); scores NOT asserted | ML settlement only; excluded from totals and score-based research |
| **Quarantined** | mirror PO 2016/2021/2023/2024 (+ partial 2015/19/20/22), 2015-18 PO series-level only | never used; detection documented |

**No verified market prices exist for any postseason game** in any reachable
source (searched; see source registry). PO "ROI" is therefore reported as a
clearly-labeled **fair-coin proxy** (2% flat at +100 = 2×(win%−50%)) — a
model-skill metric, *not* a claim about real markets.

## Headline results (walk-forward, 2015→2026)

* **REG moneyline (real 2019-25 prices):** every strategy loses small
  (Elo −0.36%, Form −0.25%, Season −0.26%, Sep-form −0.65%,
  strong-favorites ≤−150 −2.39% on 4,189 bets). Naive models do not beat the
  verified market. No longshot-bias edge at ≤−150.
* **REG totals:** Poisson run-rate beats a flat 8.5 line +1.5%/bet (25,439
  bets) — against a *synthetic* line (no verified totals exist); labeled as
  such.
* **POST (fair-coin proxy, 2019-25):** WC +40.9% (win 70.5%, n=44) · LCS
  +17.5% · DS +11.5% · **WS −12.0% (win 44%, n=25)** — the earlier rounds are
  more model-predictable and the WS is the hardest, consistent with market
  efficiency concentrating where the money is.
* **Experiment framework (A-E):** A (REG transfer) = strong broad baseline
  (FC-ROI +18.4%, n=196, Brier 0.241); C (dedicated series-state) leads on
  elimination/clinching games (+23.3%, n=60 — small, forward-test in 2026);
  E (hierarchical) +21.9% on win rate but worse calibration; B (late-season)
  rejected on calibration; D (round-specific) mixed — round identity matters.
* **Environment effects (measured, not assumed):** PO home win 0.621 vs REG
  0.533; 2025 PO total runs 8.02 vs 2025 REG 8.89 (single season, directional);
  2020-bubble scoring comparison = DATA_UNAVAILABLE (flagged, not guessed).

Full evidence: `mlbcomp/engine/research.py` → `research_findings` /
`experiments` tables, rendered on the Research page.

## Repository layout

```
mlbcomp/
  config.py            paths, constants (Kelly fraction, bankroll, seasons)
  db.py                schema + migrations + audit helpers (SQLite)
  ingest/baseballr.py  sources, canonical ids, dedup, series engine, odds join
  features/engine.py   point-in-time features (Elo/form/rates/rest)
  features/po_corpus.py  verified PO corpus builder (P1+P2)
  data_recon/po_results.py  2019-2024 reconstructed game winners (provenance)
  engine/strategies.py models + 14-strategy catalog (ID: MLB_{REG|POST}_{ROUND}_{CAT}_{NNN})
  engine/backtest.py   walk-forward competition, market gate, stats
  engine/research.py   20 questions, findings, experiments A-E
  verify/checks.py     16 verification checks (all PASS)
  web/app.py           Flask website (port 8000)
scripts/
  fetch_postseason_raw.py  direct-by-pk fetch of raw Stats API mirror dumps
  verify_postseason.py     raw-dump vs schedule cross-check (authenticity probe)
data/  (git-ignored) raw sources, features parquet, mlbcomp.db
```

## Anti-leakage contract (spec §17) — enforced in code

* Games processed strictly in start-time order; team state updates only
  *after* completion.
* Round intercepts (Model D) learned only from **prior** PO seasons, promoted
  at the PO season transition (min 15 games/round, else 0).
* Series state uses only earlier games of the same series; rest/travel
  computed point-in-time (bisect over each team's completed-game history).
* Reconstructed PO games carry synthetic 3-2 scores **only** to propagate
  state into the following season; settlement always uses the known winner.
* Uncompleted 2026 games produce OPEN/PROPOSED bets, never settlements.

## Limitations (disclosed, per spec)

1. **No postseason market prices exist in the data** → PO ROI is a fair-coin
   proxy; real PO market efficiency cannot be measured in this environment.
2. **P2 games (2019-2024 PO) have winners but no scores** → no totals, no
   scoring/volatility research for those seasons; per-game *winner*
   assignment on non-swept series is inferred from the documented series
   score (labeled per-series confidence in `po_results.py`).
3. **2025 PO score research is single-season** (n=47) — directional.
4. **2026 is single-source** (mirror snapshot through 2026-09-19) and
   unpriced until an odds source resumes.
5. **No weather, no lineups, no probable-starter identities** in the verified
   data (raw dumps carry metadata but are from the same mirror family and
   were not used) → those hypothesis families are DATA_UNAVAILABLE.
6. Kelly sizing at real prices compounds small edges into unrealistic
   bankroll numbers; treat bankroll as an artifact, ROI per dollar staked as
   the metric.
7. 2015-2018 PO are series-level only; 2021 WC/DS excluded entirely
   (insufficient reconstruction confidence — recorded, not guessed).

## Verification status

`mlbcomp/verify/checks.py` → **16/16 PASS**, including: duplicate pks,
score validity, winner consistency, doubleheader-tolerant one-game-per-day,
odds join (15,442) and winner match (14,590, 100%), PBP coverage (27,957),
2020 short season, team count (30), live-2026 snapshot, verified-corpus WS
champions 2019-25, verified-corpus series format + no-play-after-clinch, and
the mirror-fabrication quarantine check.
