# MLBComp methodology

## Operating contract

MLBComp is a research and paper-trading system. It never places a real order.
A number is published as an observation only when it has a source, retrieval
time, availability time and verification status. Unknown is a valid result.

## Environment isolation

The normalized model environment is one of:

- `REG`: regular season;
- `POST`: all postseason, used for a cross-round view only;
- `WC`, `DS`, `LCS`, `WS`: four separately versioned postseason competitions;
- `ALL`: a display roll-up that keeps each component visible.

A postseason model may use a regular-season prior, but it has its own feature
gate, model version, experiments, strategy versions and performance rows. The
round-specific models are never silently replaced by the all-postseason model.

## Point-in-time and anti-leakage rules

For a decision at `T`, a feature is eligible only when its source observation's
`availability_time <= T`. The engine processes games in chronological order,
updates team state after the decision loop, and creates series-state rows from
the state before the game. Postseason Game 3 may use Games 1 and 2; it may not
use Game 4, the final series result, a later lineup, a later injury report or
a later price.

Backtests use chronological train, validation and out-of-sample partitions.
Randomly mixing future games into training is not allowed. A model change is a
new strategy version and is compared against the previous version on the same
future window.

## Postseason hierarchy

The intended hierarchy is:

**career/multi-year → current season → late season → prior postseason →
current postseason → current game**.

The first levels can provide a prior. Current postseason and series-state
signals update the prior with shrinkage/partial pooling. No direction is
assumed for scoring, predictability, home advantage, managerial behavior,
shorter pitching leashes, bullpen fatigue, rest, travel or market efficiency;
each is a research question with a sample-size gate.

Series state contains round, series key, game number, record, games remaining,
elimination/clinching flags, home/away, previous results, rest, travel,
probable/confirmed pitcher fields and bullpen availability when those fields
are source-backed.

## Price, execution and settlement

American odds are converted to decimal and implied probability. Two-way market
probabilities are de-vigged only when both sides are observed. Kelly is a
quarter-Kelly proposal capped at five percent of the strategy bankroll. This
is risk-control math, not a claim that the model is correct.

A historical wager requires a verified quote at or before the decision time.
It records bid, ask, liquidity, available size, observed timestamp, slippage
and partial-fill status when the source provides them. Missing historical
prices are not filled with +100 and do not enter ROI/PnL. A real game result
without a quote is an `EVAL` row for calibration or directional skill only.

Settlement accepts only `W`, `L`, `P` or `V` and uses the market's documented
settlement rule. Corrected settlement is an append-only correction event.

## Metrics and conclusions

Tracked metrics include calibration, Brier score, log loss, accuracy, verified
ROI, PnL, closing-line value, drawdown, stake, liquidity/execution effects,
sample size and round/market/environment slices. A result below the minimum
sample is `INSUFFICIENT_SAMPLE`; positive small-sample results are not promoted.
Postseason results without verified prices are never described as sportsbook
ROI.

## Research loop

The catalog deliberately contains simple baselines and advanced hypotheses.
After a run, the research table stores success/failure analysis, price/value,
closing movement, data quality, signal-versus-variance assessment and the next
hypothesis. Failed strategies remain visible and are not overwritten.

## Data source registry

The registry distinguishes a discovered URL, reachability, content validation,
licensing and production eligibility. A source can be useful for discovery
without being an automated dependency. GitHub commit/blob checksums and raw
retrieval manifests are retained when a fetch occurs.
