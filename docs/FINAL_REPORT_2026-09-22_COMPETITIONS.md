# Final report — leaderboard upgrade: simulated competitions, expanded entrants & strategies, per-bet review links

Date: 2026-09-22 · Workstream: competition/leaderboard layer · All paper-only; no real-money connector exists or was added.

## Request (verbatim intent)

1. Work on the leaderboard part of the site.
2. Simulate competitions and randomize starting dates.
3. Expand the number of usernames and strategies.
4. Bet tracking must provide links to manually review every bet based on verified official prices and dates down to the second.

## What was built

### 1. Simulated competitions with randomized start dates

- New engine: `mlbcomp/engine/competition.py` (`competition-engine/v1`).
- 10 competitions over the committed verified export: five regular-season
  windows (pennant sprint '25, opening dash, summer grind, midseason blitz,
  stretch run '26), one per 2025 postseason round (WC/DS/LCS/WS), and one
  all-rounds postseason marathon.
- Start dates are randomized per competition with `random.Random` seeded from
  `sha256(rng_seed)`; the seed (`mlbcomp-competitions|2026-09-22|v1`), draw
  attempts, scored-date coverage and window bounds (UTC, second precision,
  `00:00:00Z`→`23:59:59Z`) are published per competition. Identical inputs
  reproduce identical windows; a different seed changes the windows (tested).
- A window covering no scored date is `NOT_DRAWN` with a published reason —
  never silently widened.

### 2. Expanded usernames and strategies

- **Entrants:** 57 competition personas (35 REG cohort, 22 POST cohort) in
  `data/competitors.json`. Every entrant is bound 1:1 to a published
  *strategy slice* — a declarative filter over its parent strategy's exported
  rows (side, season, probability band, rounds). Slice specs are published;
  persona assignment is round-robin so all 29 parent strategies that have
  exported rows are represented before any gets a second entrant.
- **Strategies:** the versioned catalog grew from 70 → 90 entries:
  12 REG (strict/mild entry variants, doubleheader, interleague, day-night
  turnaround, time-zone travel, starter FIP, lineup xwOBA, bullpen quality,
  park HR factor, wind, post-clinch resting) and 8 POST/round hypotheses
  (momentum checks, short-rest ace, closer availability, travel) — each with
  the full Hypothesis → Data → Entry → Required price → Sizing → Settlement →
  Test plan → Limitations contract. New entries are honest: `NOT_RUN` where
  runnable-but-unrun, `DATA_UNAVAILABLE` where their data gate is not met;
  their leaderboard rows are explicit `NO_DATA` (no synthesized performance).
- `data/leaderboard.json`/`data/strategies.json` were extended strictly
  append-only; `scripts/build_competitions.py` refuses to run if an old entry
  would be removed or reshaped, or if any protected summary field changes.

### 3. Bet tracking with manual review links

- New `data-review` UI: every row in the Ledger, Upcoming, Postseason results
  and competition picks tables carries a **Review ↗** action that opens a
  provenance panel: `SOURCE → RETRIEVAL TIME → AVAILABILITY TIME → DERIVED
  VALUE → MODEL OUTPUT → DECISION → RESULT → SETTLEMENT`, with timestamps in
  UTC to the second whenever the record carries them.
- Links target official endpoints only, keyed by `game_pk`: the MLB Stats API
  live game feed and box score, MLB Gameday, and the official schedule for
  the game date (URLs asserted in tests to be `statsapi.mlb.com` /
  `mlb.com` hosts).
- Where a record has a captured price observation, the panel also links the
  quote source URL with its observed/available timestamps. The current capped
  export holds zero such rows (see finding below), so those slots are
  explicitly flagged `NO VERIFIED QUOTE` — no price is implied for any record.

## Adversarial findings addressed in this work

1. **Capped export had zero reviewable wagers.** `_ledger()` sorted newest
   first, so the 20,000-row cap contained only `NO_MARKET_PRICE` rows
   (2025-08-29 → 2026-09-27). The export sort now puts `VERIFIED_PRICE`
   first (verified rows first, then newest), so the next capped export stays
   reviewable with real quote provenance. The committed artifact itself was
   intentionally not rewritten with invented rows.
2. **Contract drift guard fired honestly.** The first refresh attempt
   surfaced that the committed `strategies.json` had been post-promoted
   (status→BACKTESTED, alias notes patched). The builder was changed to treat
   committed entries as authoritative and append-only; it byte-preserves them.
3. **Persona starvation.** Alphabetical assignment left later strategies
   (incl. totals) without entrants; replaced with round-robin across parents
   and per-cohort pools (with deterministic fallback names for larger future
   exports).
4. **NOT_DRAWN robustness.** Specs that cannot draw on partial data used to
   raise; they now publish an explicit `NOT_DRAWN` record with the reason.
5. **Stale catalog counts.** Summary environment `strategies` counts are
   refreshed (catalog-derived), with every other summary field asserted
   unchanged; no verified performance field moved.

## Verification performed (all executed in this workspace)

- `python test/engine.test.py` — 25 tests OK (new: slice filters are pure,
  scoring uses only real outcomes, no-PnL-without-verified rows, seeded
  determinism + seed sensitivity, window second-precision and bounds,
  min-picks gate, REG/POST scope isolation, safe empty-export payloads).
- `node test/ui.test.js` — contract suite passes; it re-derives all **285**
  published standings from the ledger rows and requires exact agreement in
  picks/wins/Brier, rejects synthesized PnL, and checks seeds/bounds/links.
- `node --check app.js` — syntax OK.
- A headless jsdom smoke run rendered the site against the committed data:
  competition cards/standings, entrant drill-down with a
  "browser re-derivation matches published standings" badge, per-bet review
  modal with official links, segmented leaderboard views, and
  second-precision decision stamps all verified.
- `scripts/build_competitions.py --check` — idempotent re-run confirmed (no
  new ids, invariants hold).

## What was NOT done (honesty ledger)

- No per-bet paper PnL exists in the committed static artifact (capped export
  contains no verified-price rows), so competition standings report
  accuracy/calibration and flag PnL `UNAVAILABLE_NO_VERIFIED_PRICE_ROWS_IN_WINDOW`.
  Career PnL shown on entrant cards is the parent strategy's published
  all-history snapshot aggregate, labeled as such.
- Entrant personas are display identities over strategy slices — not
  independently retrained models. There is no claim that any slice has an
  edge; postseason windows are tiny and remain INSUFFICIENT_SAMPLE.
- The new catalog strategies have no backtest records in this snapshot; they
  publish NO_DATA metrics until the next chronological backtest covers them.

## Files

- New: `mlbcomp/engine/competition.py`, `scripts/build_competitions.py`,
  `data/competitions.json`, `data/competitors.json`, this report.
- Changed: catalog (`mlbcomp/engine/strategies.py`), export integration
  (`mlbcomp/web/export_static.py`), leaderboard/postseason/ledger UI
  (`index.html`, `app.js`, `styles.css`), tests
  (`test/engine.test.py`, `test/ui.test.js`), docs (README, METHODOLOGY),
  additive data refresh (`data/strategies.json`, `data/leaderboard.json`,
  `data/summary.json`).
