"""MLBComp configuration — constants for the autonomous MLB betting research system.

Environments (spec §1, §4, §32): the competition is split into FIVE distinct
environments. Regular-season and postseason results are never collapsed into a
single leaderboard number without preserving the breakdown.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
FEAT = DATA / "features"
DB_PATH = DATA / "mlbcomp.db"

for _p in (DATA, RAW, FEAT):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- seasons
# Regular-season backtest window with real market odds available (2019-2025,
# cesar-dx/mlb-betting-ml source). 2015-2018 are ingested for model-only
# strategies and long-horizon research but have no verified market prices.
SEASONS_ALL = list(range(2015, 2027))          # ingested (2026 = live, in progress)
SEASONS_WITH_ODDS = list(range(2019, 2026))    # verified real moneyline odds
SEASON_LIVE = 2026                              # in-progress season (paper trading)
POSTSEASON_SEASONS = list(range(2015, 2026))   # complete postseasons
TODAY = "2026-09-20"                            # data snapshot date

# ------------------------------------------------------- round codes
# baseballr schedule game_type codes: R=regular, F=first round (wild card),
# D=division series, L=league championship, W=world series, S=spring, E=exhibition.
ROUND_BY_TYPE = {"F": "WC", "D": "DS", "L": "LCS", "W": "WS"}
ROUND_LABEL = {
    "WC": "Wild Card",
    "DS": "Division Series",
    "LCS": "League Championship Series",
    "WS": "World Series",
}
ROUNDS = ["WC", "DS", "LCS", "WS"]
ROUND_FORMAT = {"WC": "BO3", "DS": "BO5", "LCS": "BO7", "WS": "BO7"}

# ------------------------------------------------------- competitions
# Each competition has its own strategies, models, backtests, ledger,
# leaderboard and bankroll (spec §4, §19).
ENVS = ["REG", "POST", "WC", "DS", "LCS", "WS", "ALL"]
ENV_LABEL = {
    "REG": "Regular Season",
    "POST": "Postseason (all rounds)",
    "WC": "Wild Card",
    "DS": "Division Series",
    "LCS": "LCS",
    "WS": "World Series",
    "ALL": "All MLB (combined)",
}
# Postseason bets also roll up into the combined environments:
ENV_ROLLUP = {"WC": ("POST", "ALL"), "DS": ("POST", "ALL"),
              "LCS": ("POST", "ALL"), "WS": ("POST", "ALL"),
              "REG": ("ALL",), "POST": ("ALL",), "ALL": ()}

# ------------------------------------------------------- bankroll / staking
STARTING_BANKROLL = 10_000.0     # per strategy, per competition
KELLY_FRACTION = 0.25            # quarter-Kelly
MAX_STAKE_PCT = 0.05             # hard cap: 5% of current bankroll
MIN_EDGE = 0.02                  # default min model edge (in prob) to bet

# ------------------------------------------------------- rejection rules
MIN_BETS_FOR_VERDICT = 30        # below this: INSUFFICIENT_SAMPLE (no verdict)
ROI_REJECT_THRESHOLD = -0.02     # ROI at or below -2% with adequate sample
ROI_PROMOTE_THRESHOLD = 0.02     # ROI above +2% AND bootstrap CI lower > 0
BOOTSTRAP_REPS = 1000
RANDOM_SEED = 42

# ------------------------------------------------------- market conversion
def am_to_prob(odds: float) -> float:
    """American odds -> implied probability (includes vig)."""
    if odds is None or odds != odds:  # NaN
        return float("nan")
    if odds < 0:
        return -odds / (-odds + 100.0)
    return 100.0 / (odds + 100.0)

def prob_to_am(p: float) -> float:
    """Probability -> American odds (no margin)."""
    if p <= 0:
        return -1000.0
    if p >= 1:
        return 1000.0
    if p <= 0.5:
        return round(100.0 * p / (1.0 - p), 1)
    return round(-100.0 * (1.0 - p) / p, 1)

def devig_two(p_home: float, p_away: float) -> tuple[float, float]:
    """Proportional devigging of a 2-way moneyline."""
    s = p_home + p_away
    if s <= 0:
        return 0.5, 0.5
    return p_home / s, p_away / s

# ------------------------------------------------------- research
# The 28 research questions from spec §28.
RESEARCH_QUESTIONS = [
    ("Q01", "Does postseason scoring differ materially from regular-season scoring?"),
    ("Q02", "Does starting-pitcher usage (leash) change in the postseason?"),
    ("Q03", "Does bullpen usage change in the postseason?"),
    ("Q04", "Does manager behavior become more predictable in the postseason?"),
    ("Q05", "Do teams shorten their lineups in the postseason?"),
    ("Q06", "Does defensive substitution increase in the postseason?"),
    ("Q07", "Does the market price postseason favorites differently?"),
    ("Q08", "Does public attention change market efficiency in the postseason?"),
    ("Q09", "Does the value of recent form change in the postseason?"),
    ("Q10", "Does late-season performance predict postseason performance better than full-season performance?"),
    ("Q11", "Does information from earlier series games improve later-game predictions?"),
    ("Q12", "Does series state create measurable behavioral effects?"),
    ("Q13", "Are elimination games materially different?"),
    ("Q14", "Are clinching games materially different?"),
    ("Q15", "Does a team's postseason experience contain measurable predictive information?"),
    ("Q16", "Does travel between rounds matter?"),
    ("Q17", "Does bullpen fatigue become more important in the postseason?"),
    ("Q18", "Does starting-pitcher leash become more important in the postseason?"),
    ("Q19", "Do market movements react differently to postseason lineup announcements?"),
    ("Q20", "Does postseason-specific modeling outperform simply applying the regular-season model?"),
]
