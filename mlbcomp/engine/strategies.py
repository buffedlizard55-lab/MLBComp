"""Models + strategy catalog (spec §15, §16, §20).

Every model is a function: (game_row, features_row, ctx) -> dict with
at least {"p_home": float}.  ctx carries shared state (Elo, series state,
learned round intercepts) that is updated only after a game completes
(anti-leakage).

Strategy IDs follow the naming convention:
    MLB_{REG|POST}_{ROUND}_{CATEGORY}_{NNN}
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..config import KELLY_FRACTION, MAX_STAKE_PCT, MIN_EDGE
from ..features.engine import poisson_win_prob


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


# ------------------------------------------------------------------ models
def model_elo(g, f, ctx) -> float:
    return f["p_elo"]


def model_form(g, f, ctx) -> float:
    """Rolling 30-game run-differential model + home advantage."""
    if f["h_n_games"] < 5 or f["a_n_games"] < 5:
        return np.nan
    return _sig(1.3 * (f["h_run_diff_pg"] - f["a_run_diff_pg"]) + 0.20)


def model_season(g, f, ctx) -> float:
    """Season-to-date (expanding) run-differential model + home advantage."""
    if f["h_season_g"] < 10 or f["a_season_g"] < 10:
        return np.nan
    return _sig(1.3 * (f["h_season_rd"] - f["a_season_rd"]) + 0.20)


def model_lateform(g, f, ctx) -> float:
    """Late-season (September, last ~15 games) form model."""
    if pd.isna(f["h_sep_rd"]) or pd.isna(f["a_sep_rd"]):
        return np.nan
    return _sig(1.3 * (f["h_sep_rd"] - f["a_sep_rd"]) + 0.20)


def model_poisson_total(g, f, ctx, line: float = 8.5) -> float:
    """Poisson run-rate model.  Returns P(OVER line) for the totals market."""
    if f["h_n_games"] < 5 or f["a_n_games"] < 5:
        return np.nan
    lh = max(0.5, f["h_rs_pg"] * f["a_ra_pg"] / 4.5)
    la = max(0.5, f["a_rs_pg"] * f["h_ra_pg"] / 4.5)
    # P(sum > line) via convolution of two Poissons
    M = 40
    ph = [math.exp(-lh) * lh ** i / math.factorial(i) for i in range(M + 1)]
    pa = [math.exp(-la) * la ** i / math.factorial(i) for i in range(M + 1)]
    conv = [0.0] * (2 * M + 1)
    for i in range(M + 1):
        if ph[i] == 0:
            continue
        for j in range(M + 1):
            conv[i + j] += ph[i] * pa[j]
    p_over = sum(conv[int(math.floor(line + 1e-9)) + 1:])
    return p_over


def model_hierarchical(g, f, ctx) -> float:
    """Hierarchical REG-prior + PO-likelihood model (spec §7, §8).

    p = w * p_observational + (1 - w) * p_prior
    where the prior is the regular-season Elo and the observational signal
    combines late-season form with actual series performance so far.  w
    grows with the amount of postseason evidence (shrinkage / partial
    pooling: the prior wins while the sample is tiny).
    """
    p_prior = f["p_elo"]
    if pd.isna(p_prior):
        return np.nan
    p_obs = _sig(1.3 * (f["h_sep_rd"] - f["a_sep_rd"]) + 0.20) \
        if not (pd.isna(f["h_sep_rd"]) or pd.isna(f["a_sep_rd"])) else 0.5
    # series evidence
    n_prior = f.get("n_series_games_before", 0) or 0
    if n_prior > 0:
        wa = f.get("wins_a_before", 0) or 0
        wb = f.get("wins_b_before", 0) or 0
        # team_a is the series' first-game home team; map to this game
        a_id = ctx["series_team_a"]
        home = int(g.home_team_id)
        my_w = wa if home == a_id else wb
        opp_w = wb if home == a_id else wa
        p_series = _sig(2.5 * (my_w - opp_w) / (1 + n_prior * 0.3))
        p_obs = 0.5 * p_obs + 0.5 * p_series
    w = min(0.8, 0.25 * n_prior) + 0.15   # base weight on observational 0.15
    return w * p_obs + (1 - w) * p_prior


def model_round_specific(g, f, ctx) -> float:
    """Round-specific model: Elo logit + learned round intercept.

    The intercept is estimated from PRIOR-season postseason games only
    (walk-forward; minimum 15 games per round, else 0).  This is the
    dedicated per-round model (Model D, spec §16).
    """
    p = f["p_elo"]
    if pd.isna(p):
        return np.nan
    rnd = g.round_code
    intercept = ctx["round_intercepts"].get(rnd, 0.0)
    return _sig(_logit(p) + intercept)


def model_xreg(g, f, ctx) -> float:
    """Model A (spec §16): the regular-season model applied unchanged to
    postseason games (control)."""
    return f["p_elo"]


def model_market_fade_longshot(g, f, ctx) -> float:
    """Market-based: model probability is the devigged market; the strategy
    (see catalog) bets favorites priced <= -150 to test the longshot bias."""
    if pd.isna(f.get("home_odds", np.nan)) or pd.isna(f.get("away_odds", np.nan)):
        return np.nan
    from ..config import am_to_prob, devig_two
    ph, pa = am_to_prob(f["home_odds"]), am_to_prob(f["away_odds"])
    ph, pa = devig_two(ph, pa)
    return ph


MODELS = {
    "elo": model_elo,
    "form": model_form,
    "season": model_season,
    "lateform": model_lateform,
    "poisson_total": model_poisson_total,
    "hierarchical": model_hierarchical,
    "round_specific": model_round_specific,
    "xreg": model_xreg,
    "market": model_market_fade_longshot,
}

# ------------------------------------------------------------------ strategies
class Strategy:
    def __init__(self, sid, name, env, model, market, hypothesis,
                 min_edge=MIN_EDGE, notes="", extra=None):
        self.sid = sid
        self.name = name
        self.env = env            # REG / WC / DS / LCS / WS / POST
        self.model = model        # key in MODELS
        self.market = market      # ML / TOTAL
        self.hypothesis = hypothesis
        self.min_edge = min_edge
        self.notes = notes
        self.extra = extra or {}

    def applies_to(self, game_row) -> bool:
        env = self.env
        if env == "REG":
            return game_row.game_type == "R"
        if env == "POST":
            return game_row.round_code is not None and not pd.isna(game_row.round_code)
        if env in ("WC", "DS", "LCS", "WS"):
            return game_row.round_code == env
        return True

    def decide(self, g, f, ctx):
        """Return dict(selection, p_model, fair_price, market) or None."""
        p = MODELS[self.model](g, f, ctx)
        if p is None or (isinstance(p, float) and (math.isnan(p) or p is None)):
            return None
        p = float(p)
        if self.market == "TOTAL":
            p_over = p
            if abs(p_over - 0.5) < self.min_edge:
                return None
            sel = "OVER" if p_over > 0.5 else "UNDER"
            line = self.extra.get("line", 8.5)
            fair = p_over if sel == "OVER" else 1 - p_over
            return {"selection": sel, "p_model": p_over, "fair_price": fair,
                    "market": "TOTAL", "line": line}
        # moneyline
        if p < 0.50 + self.min_edge and p > 0.50 - self.min_edge:
            pass  # decided below via edge
        if p >= 0.5:
            sel, p_sel = "HOME", p
        else:
            sel, p_sel = "AWAY", 1 - p
        if pd.notna(f.get("home_odds", np.nan)) and pd.notna(f.get("away_odds", np.nan)):
            from ..config import am_to_prob, devig_two
            ph, pa = devig_two(am_to_prob(f["home_odds"]), am_to_prob(f["away_odds"]))
            p_mkt = ph if sel == "HOME" else pa
            edge = p_sel - p_mkt
            if edge < self.min_edge:
                return None
        else:
            p_mkt = 0.5
            edge = p_sel - 0.5
            if edge < max(self.min_edge, 0.04):
                return None
        return {"selection": sel, "p_model": p_sel, "fair_price": p_sel,
                "market": "ML", "edge": edge, "p_market": p_mkt}


def build_catalog() -> list[Strategy]:
    return [
        # ---------------- regular season
        Strategy("MLB_REG_ELO_001", "Elo (regular season)", "REG", "elo", "ML",
                 "Elo ratings updated game-by-game contain an exploitable edge "
                 "over sportsbook prices."),
        Strategy("MLB_REG_FORM_001", "30-game form (regular season)", "REG", "form", "ML",
                 "Recent run differential predicts wins better than the market."),
        Strategy("MLB_REG_SEASON_001", "Season-to-date strength (regular season)", "REG",
                 "season", "ML", "Full-season run differential contains an edge."),
        Strategy("MLB_REG_LATEFORM_001", "Late-season form (regular season)", "REG",
                 "lateform", "ML", "September form is more predictive than full-season "
                 "averages (spec §28 Q09)."),
        Strategy("MLB_REG_TOTALS_001", "Poisson run-rate totals", "REG", "poisson_total",
                 "TOTAL", "Poisson run-rate model beats a flat 8.5 line.",
                 extra={"line": 8.5}),
        Strategy("MLB_REG_FAV_BIAS_001", "Strong favorites (regular season)", "REG",
                 "elo", "ML",
                 "Longshot bias: strong favorites priced <= -150 are "
                 "underpriced by the market (Elo signal, market filter).",
                 min_edge=-1.0,  # gating happens in special_rules
                 extra={"fav_max": -150.0}),
        # ---------------- postseason (round-specific)
        Strategy("MLB_POST_WC_ELO_001", "Wild Card Elo", "WC", "round_specific", "ML",
                 "Round-specific Elo intercept (learned from prior seasons) "
                 "improves Wild Card predictions."),
        Strategy("MLB_POST_DS_ELO_001", "Division Series Elo", "DS", "round_specific", "ML",
                 "Round-specific Elo intercept improves Division Series predictions."),
        Strategy("MLB_POST_LCS_ELO_001", "LCS Elo", "LCS", "round_specific", "ML",
                 "Round-specific Elo intercept improves LCS predictions."),
        Strategy("MLB_POST_WS_ELO_001", "World Series Elo", "WS", "round_specific", "ML",
                 "Round-specific Elo intercept improves World Series predictions."),
        Strategy("MLB_POST_HIERARCHICAL_001", "Hierarchical REG+PO model", "POST",
                 "hierarchical", "ML",
                 "A hierarchical model (regular-season prior + postseason likelihood "
                 "with shrinkage) outperforms either environment alone (spec §7, §16E)."),
        Strategy("MLB_POST_LATESEASON_001", "Late-season prior (postseason)", "POST",
                 "lateform", "ML",
                 "Late-regular-season form predicts postseason games better than the "
                 "full season (spec §28 Q10)."),
        Strategy("MLB_POST_SERIESSTATE_001", "Series state (elimination/clinching)",
                 "POST", "elo", "ML",
                 "Series state (elimination/clinching) creates a measurable behavioral "
                 "edge (spec §28 Q12-14).", min_edge=0.03),
        Strategy("MLB_POST_XREG_001", "Regular-season model on PO (control)", "POST",
                 "xreg", "ML",
                 "Control: the unadjusted regular-season model applied to postseason "
                 "games (Model A, spec §16)."),
    ]


def special_rules(sid: str, g, f, ctx, decision) -> dict | None:
    """Extra decision rules for strategies whose logic is not pure edge."""
    if sid == "MLB_REG_FAV_BIAS_001":
        # Bet the MARKET FAVORITE only when it is a strong favorite
        # (odds <= -150) AND the model independently agrees it is more likely
        # to win.  This tests the longshot-bias hypothesis directly.
        ho = f.get("home_odds", np.nan)
        ao = f.get("away_odds", np.nan)
        if pd.isna(ho) or pd.isna(ao):
            return None
        fav_is_home = ho < ao
        fav_odds = ho if fav_is_home else ao
        if fav_odds > ctx["fav_max"]:
            return None
        # model prob on the favorite side
        p_home = (decision["p_model"] if decision["selection"] == "HOME"
                  else 1 - decision["p_model"])
        p_fav = p_home if fav_is_home else 1 - p_home
        if p_fav < 0.5 + 0.02:  # model must agree the favorite is favored
            return None
        sel = "HOME" if fav_is_home else "AWAY"
        from ..config import am_to_prob, devig_two
        ph, pa = devig_two(am_to_prob(ho), am_to_prob(ao))
        p_mkt_fav = ph if fav_is_home else pa
        return {"selection": sel, "p_model": p_fav, "fair_price": p_fav,
                "market": "ML", "edge": p_fav - p_mkt_fav, "p_market": p_mkt_fav}
    if sid == "MLB_POST_SERIESSTATE_001":
        # only play elimination or clinching games
        elim = (f.get("elimination_a", 0) == 1) or (f.get("elimination_b", 0) == 1)
        clin = (f.get("clinch_a", 0) == 1) or (f.get("clinch_b", 0) == 1)
        if not (elim or clin):
            return None
        return decision
    return decision
