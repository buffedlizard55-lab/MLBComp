"""Configuration and shared quantitative conventions for MLBComp.

The project is deliberately conservative: an observed value is either backed by
an immutable provenance record or it is marked unavailable.  In particular,
missing historical odds are *not* converted into a made-up +100 quote.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
FEAT = DATA / "features"
DB_PATH = DATA / "mlbcomp.db"
for _path in (DATA, RAW, FEAT):
    _path.mkdir(parents=True, exist_ok=True)

# The snapshot date is explicit and overridable for reproducible rebuilds.
TODAY = os.environ.get("MLBCOMP_AS_OF", "2026-09-21")
SEASON_LIVE = 2026
SEASONS_ALL = list(range(2015, SEASON_LIVE + 1))
SEASONS_WITH_ODDS = list(range(2019, 2026))
POSTSEASON_SEASONS = list(range(2015, 2026))

ROUND_BY_TYPE = {"F": "WC", "D": "DS", "L": "LCS", "W": "WS"}
ROUNDS = ["WC", "DS", "LCS", "WS"]
ROUND_LABEL = {
    "WC": "Wild Card",
    "DS": "Division Series",
    "LCS": "League Championship Series",
    "WS": "World Series",
}


def round_needed(round_code: str, season: int) -> int:
    """Wins required to clinch a postseason series (format changes by year).

    Source: MLB official postseason format history (mlb.com/news/mlb-playoff-
    format-faq, verified 2026-09-21): the single-elimination Wild Card Game
    was used 2012-2019 and again in 2021; the best-of-three Wild Card Series
    debuted in the expanded 2020 postseason and returned permanently in 2022.
    DS is best-of-5 and LCS/WS best-of-7 in every season in scope (the 2020 DS
    remained best-of-five at neutral sites per MLB's 2020-07-23 announcement).
    """
    if round_code == "WC":
        if season == 2020 or season >= 2022:
            return 2
        return 1
    if round_code == "DS":
        return 3
    if round_code in {"LCS", "WS"}:
        return 4
    raise ValueError(f"unknown postseason round {round_code!r}")


def round_format_label(round_code: str, season: int) -> str:
    n = round_needed(round_code, season)
    return {1: "BO1", 2: "BO3", 3: "BO5", 4: "BO7"}[n]

# Environments are intentionally not aliases.  POST is the all-round
# postseason competition; WC/DS/LCS/WS are four separate competitions.
ENVS = ["REG", "POST", "WC", "DS", "LCS", "WS", "ALL"]
ENV_LABEL = {
    "REG": "Regular Season",
    "POST": "Postseason — all rounds",
    "WC": "Wild Card",
    "DS": "Division Series",
    "LCS": "League Championship Series",
    "WS": "World Series",
    "ALL": "All MLB — breakdown preserved",
}
ENV_ROLLUP = {"REG": ("ALL",), "POST": ("ALL",),
              "WC": ("POST", "ALL"), "DS": ("POST", "ALL"),
              "LCS": ("POST", "ALL"), "WS": ("POST", "ALL"), "ALL": ()}

STARTING_BANKROLL = 10_000.0
KELLY_FRACTION = 0.25
MAX_STAKE_PCT = 0.05
MIN_EDGE = 0.02
MIN_BETS_FOR_VERDICT = 30
BOOTSTRAP_REPS = 1_000
RANDOM_SEED = 42

# Explicit market names used across the normalized market, strategy and UI
# layers.  A market can be in the catalog without being available in a source.
MARKETS = [
    "ML", "RL", "TOTAL", "TEAM_TOTAL", "F5_ML", "F5_TOTAL", "NRFI", "YRFI",
    "INNING", "PLAYER_PROP", "PITCHER_PROP", "ALT_LINE", "FUTURES", "LIVE",
    "EXCHANGE", "PREDICTION_MARKET",
]


def am_to_prob(odds: float | int | None) -> float:
    """Return the vig-inclusive implied probability for valid American odds."""
    if odds is None:
        return float("nan")
    try:
        value = float(odds)
    except (TypeError, ValueError):
        return float("nan")
    if not math.isfinite(value) or value == 0:
        return float("nan")
    return -value / (-value + 100.0) if value < 0 else 100.0 / (value + 100.0)


def american_to_decimal(odds: float | int) -> float:
    value = float(odds)
    if not math.isfinite(value) or value == 0:
        raise ValueError("American odds must be finite and non-zero")
    return 1.0 + (100.0 / abs(value) if value < 0 else value / 100.0)


def prob_to_am(probability: float) -> float:
    """Convert a fair probability to American odds without rounding away edge."""
    p = float(probability)
    if not 0 < p < 1:
        raise ValueError("probability must be strictly between zero and one")
    return round(100.0 * p / (1.0 - p), 1) if p <= 0.5 else round(-100.0 * (1.0 - p) / p, 1)


def devig_two(home_probability: float, away_probability: float) -> tuple[float, float]:
    """Proportional two-way de-vig.  Invalid books return NaNs, never 50/50."""
    h, a = float(home_probability), float(away_probability)
    if not (math.isfinite(h) and math.isfinite(a) and h > 0 and a > 0):
        return float("nan"), float("nan")
    total = h + a
    return h / total, a / total


# Research questions are stored in the DB as durable hypotheses.  Findings are
# only generated when the required fields pass their point-in-time data gate.
RESEARCH_QUESTIONS = [
    ("Q01", "Does postseason scoring differ from regular-season scoring?"),
    ("Q02", "Does postseason run-margin volatility differ from regular season?"),
    ("Q03", "Does home advantage differ by environment or round?"),
    ("Q04", "Does starting-pitcher usage or leash change in postseason?"),
    ("Q05", "Does bullpen workload or availability change in postseason?"),
    ("Q06", "Does lineup construction or platoon usage change in postseason?"),
    ("Q07", "Do managerial substitutions or defensive changes change in postseason?"),
    ("Q08", "Does pitch mix or velocity retain a postseason-specific effect?"),
    ("Q09", "Does late-season form add information beyond multi-year and season priors?"),
    ("Q10", "Does series state affect game outcomes after controlling for team strength?"),
    ("Q11", "Are elimination and clinching games measurably different?"),
    ("Q12", "Does game number or home-field pattern matter within a series?"),
    ("Q13", "Do rest and travel affect postseason performance?"),
    ("Q14", "Does the market price postseason favorites or public attention differently?"),
    ("Q15", "Does regular-season transfer outperform postseason adjustment?"),
    ("Q16", "Does a dedicated postseason model outperform the transfer control?"),
    ("Q17", "Do round-specific models outperform one all-postseason model?"),
    ("Q18", "Does hierarchical partial pooling improve calibration?"),
    ("Q19", "Which market families have reliable historical prices?"),
    ("Q20", "Do model changes improve out-of-sample performance?"),
]

VERIFICATION_STATUSES = {
    "VERIFIED",
    "PARTIALLY_VERIFIED",
    "NOT_VERIFIED",
    "UNAVAILABLE",
    "CONFLICTING",
    "REJECTED",
}

# Distinguish the reason a result is absent.  These strings are part of the
# static-data contract consumed by the website.
DATA_STATUSES = {
    "VERIFIED", "UNVERIFIED", "DATA_UNAVAILABLE", "NOT_RUN", "INSUFFICIENT_SAMPLE",
    "PROPOSED", "OPEN", "SETTLED", "VOID", "CORRECTED",
}
