# MLBComp — Methodology & Mathematical Foundations

## 1. Executive Summary & Design Principles

MLBComp is an autonomous quantitative research and strategy competition platform designed to discover, evaluate, calibrate, and live paper-trade baseball betting strategies across multiple market types under strict point-in-time and zero-lookahead conditions.

The system evaluates **58 autonomous personas** across **17 distinct quantitative disciplines**, covering:
- Pitcher Leash & Starter ERA/FIP
- Bullpen Usage & High-Leverage Fatigue
- Platoon Advantage & Handedness Splits
- Ballpark Factors & Altitude Adjustments
- Wind, Humidor & Weather Dynamics
- Rest, Travel & Getaway Day Situations
- Statistical & Machine Learning Models (Elo, Pythagorean, Poisson, Bayesian, Logistic, Monte Carlo, LightGBM, Ensemble)
- Market Movement & Steam Tracking (Reverse Line Movement, Pinnacle/Circa sharp tracking)
- Kalshi CFTC Prediction Markets
- Manager In-Game Strategy & Hook Tendencies
- Umpire Strike Zone Tendencies
- Public Contrarian & Line Fades
- Player Prop Strategies (Pitcher Ks, Total Bases, Outs Recorded)
- First 5 Innings (F5) Derivatives
- Run Line (-1.5 / +1.5) & Alt Markets
- Team Totals & Run Production
- Postseason Environment & Series State

---

## 2. Environment Segregation (Spec §1, §4, §32)

MLBComp enforces a strict separation between Regular Season and Postseason competitions:

1. **Regular Season (`REG`):**
   - Evaluated against **15,442 verified real market closing moneylines** from `cesar-dx/mlb-betting-ml` (2019–2025).
   - Realized PnL and ROI reflect actual market pricing, closing line value (CLV), and bookmaker margin (vig).
   - In-progress 2026 season games (through September 20, 2026) are tracked live as paper trades.

2. **Postseason (`POST`, `WC`, `DS`, `LCS`, `WS`):**
   - No verified market prices exist in reachable open-source historical records.
   - Evaluated strictly using a **Fair-Coin Proxy**:
     $$\text{ROI}_{\text{proxy}} = 2 \times (\text{Win\%} - 50\%)$$
   - Represents pure model skill against a hypothetical zero-vig +100 market.
   - Postseason results are never collapsed into a single leaderboard figure without explicitly preserving the environment breakdown.

---

## 3. Mathematical Formulations

### 3.1 FiveThirtyEight Calibrated MLB Elo Engine
Team ratings update dynamically after each completed official game $t$:
$$P(\text{Home Win}) = \frac{1}{1 + 10^{-(\text{Elo}_{\text{Home}} - \text{Elo}_{\text{Away}} + \text{HFA}) / 400}}$$
where $\text{HFA} \approx 24.0$ Elo points (~53.3% home win frequency).

The margin-of-victory multiplier $M$ scales update sensitivity:
$$M = \frac{(|\text{Run Diff}| + 3)^{0.8}}{7.5 + 0.006 \times |\Delta \text{Elo}|}$$
$$\text{Elo}_{t+1} = \text{Elo}_t + K \times M \times (\text{Outcome} - P)$$

Offseason regression reverts ratings 33% toward the historical mean (1500.0).

### 3.2 Bivariate Poisson Scoring Model
Independent Poisson distributions simulate score grids across $0 \le x, y \le 40$:
$$P(\text{Home} = x, \text{Away} = y) = \frac{\lambda_H^x e^{-\lambda_H}}{x!} \times \frac{\lambda_A^y e^{-\lambda_A}}{y!}$$
where:
$$\lambda_H = \max\left(0.5, \frac{\text{RS}_H \times \text{RA}_A}{\text{League Mean}}\right), \quad \lambda_A = \max\left(0.5, \frac{\text{RS}_A \times \text{RA}_H}{\text{League Mean}}\right)$$

### 3.3 Quarter-Kelly Risk Management
Stakes are sized proportionally to calculated edge under fractional Kelly:
$$f^* = \frac{b \cdot p - q}{b} \times 0.25$$
subject to a hard constraint:
$$\text{Stake} \le 0.05 \times \text{Current Bankroll}$$

---

## 4. Anti-Leakage & Zero-Lookahead Contract

- Chronological event ordering: Games are processed strictly by start time.
- Contextual isolation: Features for game $t$ contain only data from timestamps $< t$.
- Quarantined fabrication: Sportsdataverse schedule mirror's fabricated postseason games are isolated and excluded.
- Reconstructed corpus integrity: 2019–2024 postseason games carry documented confidence ratings and are restricted to moneyline settlement.
