"""Autonomous strategy catalog and point-in-time model functions.

The catalog is intentionally broad, but a catalog entry is not a performance
claim.  Each entry carries its data gate and is ``DATA_UNAVAILABLE`` until the
required observations (for example an announced lineup or a timestamped prop
quote) are actually present.  Regular-season and postseason entries are never
shared implicitly; postseason round codes are explicit.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..config import MARKETS, MIN_EDGE, prob_to_am


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, float(x)))))


def _logit(p: float) -> float:
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _num(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- models

def model_elo(g, f, ctx) -> float:
    return float(f["p_elo"])


def model_form(g, f, ctx) -> float:
    if f.get("h_n_games", 0) < 5 or f.get("a_n_games", 0) < 5:
        return float("nan")
    return _sig(1.3 * (f["h_run_diff_pg"] - f["a_run_diff_pg"]) + 0.20)


def model_season(g, f, ctx) -> float:
    if f.get("h_season_g", 0) < 10 or f.get("a_season_g", 0) < 10:
        return float("nan")
    return _sig(1.3 * (f["h_season_rd"] - f["a_season_rd"]) + 0.20)


def model_lateform(g, f, ctx) -> float:
    if not (_num(f.get("h_sep_rd")) and _num(f.get("a_sep_rd"))):
        return float("nan")
    return _sig(1.3 * (f["h_sep_rd"] - f["a_sep_rd"]) + 0.20)


def model_poisson_total(g, f, ctx, line: float | None = None) -> float:
    if f.get("h_n_games", 0) < 5 or f.get("a_n_games", 0) < 5:
        return float("nan")
    # The probability must be evaluated at the SAME line that will settle it:
    # a source-observed line when verified, otherwise the strategy's model
    # hypothesis line (never presented as a sportsbook price).
    if line is None:
        observed = f.get("observed_total_line")
        line = float(observed) if _num(observed) else 8.5
    lh = max(0.5, float(f["h_rs_pg"]) * float(f["a_ra_pg"]) / 4.5)
    la = max(0.5, float(f["a_rs_pg"]) * float(f["h_ra_pg"]) / 4.5)
    limit = 40
    total_prob = 0.0
    for i in range(limit + 1):
        pi = math.exp(-lh) * lh ** i / math.factorial(i)
        for j in range(limit + 1):
            if i + j > line:
                total_prob += pi * math.exp(-la) * la ** j / math.factorial(j)
    return min(max(total_prob, 0.0), 1.0)


def model_hierarchical(g, f, ctx) -> float:
    prior = f.get("p_elo", float("nan"))
    if not _num(prior):
        return float("nan")
    late = model_lateform(g, f, ctx)
    obs = late if _num(late) else 0.5
    n = int(ctx.get("series", {}).get(str(getattr(g, "series_key", "")), {}).get("n", 0))
    state = ctx.get("series", {}).get(str(getattr(g, "series_key", "")), {})
    if n:
        a_id = state.get("a")
        wins = state.get("wa", 0) if int(g.home_team_id) == a_id else state.get("wb", 0)
        losses = state.get("wb", 0) if int(g.home_team_id) == a_id else state.get("wa", 0)
        obs = 0.5 * obs + 0.5 * _sig(1.8 * (wins - losses) / (1.0 + 0.25 * n))
    weight = min(0.8, 0.15 + 0.20 * n)
    return float(weight * obs + (1.0 - weight) * prior)


def model_round_specific(g, f, ctx) -> float:
    p = f.get("p_post_elo", f.get("p_elo", float("nan")))
    if not _num(p):
        p = f.get("p_elo", float("nan"))
    if not _num(p):
        return float("nan")
    intercept = ctx.get("round_intercepts", {}).get(getattr(g, "round_code", None), 0.0)
    return _sig(_logit(p) + float(intercept))


def model_xreg(g, f, ctx) -> float:
    # Permanent control: regular-season model transferred unchanged.
    return model_elo(g, f, ctx)


def model_post_elo(g, f, ctx) -> float:
    """Dedicated postseason Elo: REG rating is the prior, then only earlier PO games update it.

    Distinct from transfer (xreg) which never updates on postseason results, and
    from hierarchical pooling which mixes late-form and series state.
    """
    p = f.get("p_post_elo", float("nan"))
    if not _num(p):
        p = f.get("p_elo", float("nan"))
    return float(p) if _num(p) else float("nan")


def model_market(g, f, ctx) -> float:
    # A historical price without a verified availability timestamp is not an
    # eligible model feature; using it would leak closing information.
    if not f.get("market_quote_verified", False):
        return float("nan")
    from ..config import am_to_prob, devig_two
    h, a = am_to_prob(f.get("home_odds")), am_to_prob(f.get("away_odds"))
    h, a = devig_two(h, a)
    return h


def _logit_clip(p: float) -> float:
    return _logit(p)


def _sigmoid(x: float) -> float:
    return _sig(x)


def _online_adjust(f: dict, ctx: dict, env_key: str, x: float | None) -> float | None:
    """Online single-coefficient logistic adjustor over an Elo logit offset.

    The coefficient starts at 0 (pure baseline) and is updated AFTER each
    completed game by plain SGD on log loss, so a prediction only ever uses
    coefficients fit on strictly earlier games (point-in-time safe).
    Returns None when the feature is unavailable.
    """
    if x is None or not _num(x) or not _num(f.get("p_elo")):
        return None
    store = ctx.setdefault("online_coefs", {})
    slot = store.setdefault(env_key, {"w": 0.0, "n": 0})
    return float(slot["w"])


def _online_update(ctx: dict, env_key: str, x: float, p_model_home: float,
                   home_won: int, l2: float = 0.01) -> None:
    """Post-game SGD step for the online coefficient (never sees future data)."""
    if not (_num(x) and _num(p_model_home)):
        return
    store = ctx.setdefault("online_coefs", {})
    slot = store.setdefault(env_key, {"w": 0.0, "n": 0})
    err = p_model_home - home_won          # d log-loss / d logit
    n = slot["n"]
    lr = 0.05 / (1.0 + n / 2000.0)
    slot["w"] = float(np.clip(slot["w"] - lr * (err * x + l2 * slot["w"]), -1.5, 1.5))
    slot["n"] = n + 1


def model_bullpen(g, f, ctx) -> float:
    """Elo adjusted by point-in-time bullpen workload differential.

    x = (away bullpen batters faced last 3 team games − home's)/10: a more
    worked away bullpen is hypothesized to help the home side.  The sign and
    size are LEARNED online from earlier completed games, never assumed.
    """
    base = model_elo(g, f, ctx)
    if not _num(base):
        return float("nan")
    bh, ba = f.get("h_bp_bf_l3"), f.get("a_bp_bf_l3")
    if not (_num(bh) and _num(ba)):
        return float("nan")
    env = "POST" if pd.notna(getattr(g, "round_code", np.nan)) else "REG"
    w = _online_adjust(f, ctx, f"bp_{env}", 1.0)
    x = (float(ba) - float(bh)) / 10.0
    return _sigmoid(_logit_clip(min(max(base, 1e-6), 1 - 1e-6)) + (w or 0.0) * x)


def model_rest(g, f, ctx) -> float:
    """Elo adjusted by rest-day differential (home − away), coefficient learned."""
    base = model_elo(g, f, ctx)
    if not _num(base):
        return float("nan")
    rh, ra = f.get("rest_home"), f.get("rest_away")
    if not (_num(rh) and _num(ra)):
        return float("nan")
    env = "POST" if pd.notna(getattr(g, "round_code", np.nan)) else "REG"
    w = _online_adjust(f, ctx, f"rest_{env}", 1.0)
    x = (float(rh) - float(ra))
    x = max(min(x, 4.0), -4.0)
    return _sigmoid(_logit_clip(min(max(base, 1e-6), 1 - 1e-6)) + (w or 0.0) * x)


def model_market_move(g, f, ctx) -> float:
    """Open line adjusted by observed open→close movement (known at first pitch).

    Entry is at the close: both the opening price and the closing price are
    available at the decision timestamp, so the movement feature carries no
    look-ahead.  The coefficient on movement starts at 0 and is learned
    online from earlier completed games.
    """
    if not f.get("market_quote_verified", False):
        return float("nan")
    from ..config import am_to_prob, devig_two
    h_open, a_open = devig_two(am_to_prob(f.get("home_odds")), am_to_prob(f.get("away_odds")))
    h_close, a_close = devig_two(am_to_prob(f.get("market_close_home_odds")),
                                 am_to_prob(f.get("market_close_away_odds")))
    if not all(_num(v) for v in (h_open, h_close)):
        return float("nan")
    env = "POST" if pd.notna(getattr(g, "round_code", np.nan)) else "REG"
    w = _online_adjust(f, ctx, f"mv_{env}", 1.0)
    movement = float(h_close) - float(h_open)   # positive = closed harder on home
    base_logit = _logit_clip(min(max(float(h_close), 1e-6), 1 - 1e-6))
    return _sigmoid(base_logit + (w or 0.0) * movement * 4.0)


MODELS: dict[str, Callable] = {
    "elo": model_elo, "form": model_form, "season": model_season,
    "lateform": model_lateform, "poisson_total": model_poisson_total,
    "hierarchical": model_hierarchical, "round_specific": model_round_specific,
    "xreg": model_xreg, "post_elo": model_post_elo, "market": model_market,
    "bullpen": model_bullpen, "rest": model_rest, "market_move": model_market_move,
}


@dataclass
class Strategy:
    sid: str
    name: str
    env: str
    model: str
    market: str
    hypothesis: str
    data_requirements: tuple[str, ...] = ()
    entry_rule: str = "Only evaluate when every required input is available before the decision timestamp."
    required_price_rule: str = "Require an observed, timestamped quote at or before the decision timestamp; otherwise EVAL/PROPOSED, never a wager."
    sizing_rule: str = "Quarter Kelly on the observed price, capped at 5% of strategy bankroll; no stake without a price."
    settlement_rule: str = "Use the market's documented settlement rule and an independently observed result; W/L/P/V only."
    test_plan: str = "Chronological train/validate/out-of-sample test, then forward test and paper trade."
    limitations: str = "No causal claim; small samples remain inconclusive."
    min_edge: float = MIN_EDGE
    notes: str = ""
    version: str = "v1"
    parent_version: str | None = None
    status: str = "NOT_RUN"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def strategy_id(self) -> str:
        return self.sid

    def applies_to(self, game_row) -> bool:
        round_code = getattr(game_row, "round_code", None)
        game_type = getattr(game_row, "game_type", None)
        if self.env == "REG":
            return game_type == "R" or (pd.isna(round_code) if round_code is not None else True)
        if self.env == "POST":
            return round_code in {"WC", "DS", "LCS", "WS"}
        if self.env in {"WC", "DS", "LCS", "WS"}:
            return round_code == self.env
        return False

    def model_probability(self, g, f, ctx) -> float:
        if self.model not in MODELS:
            return float("nan")
        try:
            p = MODELS[self.model](g, f, ctx)
            return float(p) if _num(p) else float("nan")
        except (KeyError, TypeError, ValueError, OverflowError):
            return float("nan")

    def decide(self, g, f, ctx) -> dict[str, Any] | None:
        p_home = self.model_probability(g, f, ctx)
        if not _num(p_home):
            return None
        p_home = min(max(p_home, 1e-6), 1 - 1e-6)
        if self.market in {"TOTAL", "F5_TOTAL", "TEAM_TOTAL"}:
            # Use the source-observed line when one exists (EVAL settlement);
            # the strategy default is a model hypothesis line, never a claim
            # about an unobserved sportsbook total.
            observed_line = f.get("observed_total_line")
            if _num(observed_line) and f.get("ou_line_source"):
                line = float(observed_line)
                from_source = True
            else:
                line = float(self.extra.get("line", 8.5))
                from_source = False
            p_over = p_home
            selection = "OVER" if p_over >= 0.5 else "UNDER"
            p_selection = p_over if selection == "OVER" else 1.0 - p_over
            if p_selection < 0.5 + self.min_edge:
                return None
            return {"selection": f"{selection} {line:g}", "p_model": p_selection,
                    "fair_price": p_selection, "required_price": prob_to_am(p_selection),
                    "market": self.market, "line": line, "edge": None,
                    "observed_line": line if from_source else None,
                    "price_tier": None}
        selection = "HOME" if p_home >= 0.5 else "AWAY"
        p_selection = p_home if selection == "HOME" else 1.0 - p_home
        # The price gate is applied by the runner against a quote.  A no-price
        # prediction can still be evaluated for Brier/log-loss.
        return {"selection": selection, "p_model": p_selection,
                "fair_price": p_selection, "required_price": prob_to_am(p_selection),
                "market": self.market, "edge": None,
                "price_tier": self.extra.get("price_tier", "open")}

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record.update({"id": self.sid, "strategy_id": self.sid,
                       "env": self.env, "data_requirements": list(self.data_requirements),
                       "env_label": self.env})
        return record


# ---------------------------------------------------------------- catalog helpers

def _s(sid: str, name: str, env: str, model: str, market: str, hypothesis: str,
       requirements: tuple[str, ...], **kwargs) -> Strategy:
    return Strategy(sid, name, env, model, market, hypothesis, requirements, **kwargs)


def build_catalog() -> list[Strategy]:
    """Return the complete research library, including hypotheses not yet runnable."""
    reg: list[Strategy] = [
        _s("MLB_REG_ELO_001", "Elo baseline", "REG", "elo", "ML",
           "A time-decayed team-strength baseline is calibrated before first pitch.", ("games.scores",)),
        _s("MLB_REG_FORM_001", "Thirty-game form", "REG", "form", "ML",
           "Recent run differential contains information after controlling for long-run strength.", ("games.scores",)),
        _s("MLB_REG_SEASON_001", "Season-to-date strength", "REG", "season", "ML",
           "Expanding season run differential is a useful pre-game strength signal.", ("games.scores",)),
        _s("MLB_REG_LATEFORM_001", "Late-season form", "REG", "lateform", "ML",
           "September form adds information beyond the full season.", ("games.scores",)),
        _s("MLB_REG_TOTALS_001", "Poisson run-rate totals", "REG", "poisson_total", "TOTAL",
           "A point-in-time run-rate distribution forecasts totals; no line is invented when absent.", ("games.scores", "markets.total")),
        _s("MLB_REG_FAV_BIAS_001", "Favorite-price test", "REG", "elo", "ML",
           "Strong-favorite pricing may differ from a calibrated team-strength probability.", ("games.scores", "markets.moneyline"), min_edge=0.0, extra={"fav_max": -150.0}),
        _s("MLB_REG_STARTER_001", "Starting-pitcher prior", "REG", "elo", "ML",
           "Verified starter quality and handedness improve the team baseline.", ("games.scores", "starting_pitchers", "statistics.pitching"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_STARTER_SPLIT_001", "Starter matchup splits", "REG", "elo", "ML",
           "Pitcher/batter handedness and pitch-quality splits are predictive pre-game.", ("starting_pitchers", "statistics.splits"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_PITCH_MIX_001", "Pitch mix and velocity", "REG", "elo", "ML",
           "Recent pitch mix/velocity changes adjust starter expectations.", ("statcast.pitch_level",), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_BULLPEN_001", "Bullpen workload", "REG", "bullpen", "ML",
           "Point-in-time bullpen workload (batters faced over the last three team "
           "games, play-by-play derived) adjusts the team-strength baseline; the "
           "coefficient is learned online from strictly earlier games.",
           ("games.scores", "bullpen_usage")),
        _s("MLB_REG_BULLPEN_FATIGUE_001", "Bullpen fatigue", "REG", "elo", "ML",
           "Back-to-back usage plus official pitch counts affect late-game win probability.",
           ("bullpen_usage", "statistics.pitch_counts"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_REST_001", "Rest differential", "REG", "rest", "ML",
           "Days since each team's last completed game adjust the baseline; the "
           "coefficient is learned online from strictly earlier games.",
           ("games.scores", "schedule")),
        _s("MLB_REG_LEVERAGE_001", "High-leverage reliever usage", "REG", "elo", "ML",
           "Available high-leverage relievers matter after the starter exits.", ("bullpen_usage", "statistics.leverage"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_LINEUP_001", "Confirmed lineup strength", "REG", "elo", "ML",
           "Confirmed batting-order quality shifts pre-game probability.", ("lineups", "players.statistics"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_PLATOON_001", "Platoon advantage", "REG", "elo", "ML",
           "Verified batter/pitcher handedness splits improve lineup estimates.", ("lineups", "statistics.splits"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_INJURY_001", "Injury availability", "REG", "elo", "ML",
           "Timestamped injury/news availability changes the lineup prior.", ("injuries", "lineups"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_STATCAST_001", "Statcast contact quality", "REG", "elo", "ML",
           "Point-in-time quality-of-contact trends add signal beyond runs.", ("statcast.pitch_level",), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_DEFENSE_001", "Defense and baserunning", "REG", "elo", "ML",
           "Defense and baserunning ratings improve expected run prevention.", ("statistics.fielding", "statistics.baserunning"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_WEATHER_001", "Weather and roof state", "REG", "elo", "ML",
           "Forecast available before first pitch changes expected scoring.", ("weather.forecast", "venues"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_PARK_001", "Park run environment", "REG", "elo", "TOTAL",
           "Park and roof effects improve a run-distribution forecast.", ("venues", "weather"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_UMPIRE_001", "Umpire zone", "REG", "elo", "TOTAL",
           "Timestamped umpire assignments and zone tendencies affect totals.", ("umpires", "statistics.zone"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_TRAVEL_001", "Travel distance", "REG", "elo", "ML",
           "Distance and time-zone changes beyond rest days affect team performance.",
           ("travel_rest", "schedule"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_SCHEDULE_001", "Scheduling spots", "REG", "elo", "ML",
           "Getaway days, doubleheaders and opponent sequence create measurable effects.", ("schedule",), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_MARKET_MOVE_001", "Opening-to-close movement", "REG", "market_move", "ML",
           "Movement from the verified opening line to the close (both known at "
           "first pitch) adds information beyond the closing price itself; entry "
           "is at the close and the movement coefficient is learned online.",
           ("markets.moneyline.open_current",), extra={"price_tier": "close"}),
        _s("MLB_REG_CLOSING_001", "Closing-line benchmark", "REG", "market", "ML",
           "The de-vigged closing favorite is the market-efficiency benchmark: "
           "calibration against it measures book hold, not a model edge. Entry is "
           "at the closing price on verified rows only.",
           ("markets.moneyline.close",), extra={"price_tier": "close"}, min_edge=0.0),
        _s("MLB_REG_F5_001", "First-five moneyline", "REG", "elo", "F5_ML",
           "Starter-focused probability can differ from full-game market price.", ("starting_pitchers", "markets.f5"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_F5_TOTAL_001", "First-five total", "REG", "poisson_total", "F5_TOTAL",
           "Starter run distribution forecasts a verified first-five line.", ("starting_pitchers", "markets.f5_total"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_RL_001", "Run-line value", "REG", "elo", "RL",
           "A score-distribution model prices the run line only when quoted.", ("games.scores", "markets.run_line"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_TEAM_TOTAL_001", "Team total", "REG", "poisson_total", "TEAM_TOTAL",
           "Team-specific run rate forecasts a verified team total.", ("games.scores", "markets.team_total"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_PLAYER_K_001", "Pitcher strikeout prop", "REG", "elo", "PITCHER_PROP",
           "Pitch-level strikeout expectation is compared with a timestamped prop quote.", ("starting_pitchers", "statcast.pitch_level", "markets.player_props"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_PLAYER_HIT_001", "Batter hit prop", "REG", "elo", "PLAYER_PROP",
           "Batter opportunity and quality are compared with an observed player prop.", ("lineups", "statcast", "markets.player_props"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_ALT_001", "Alternate-line distribution", "REG", "elo", "ALT_LINE",
           "A full score distribution is evaluated at alternate prices without cherry-picking.", ("games.scores", "markets.alternate"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_LIVE_001", "Live state update", "REG", "elo", "LIVE",
           "In-game state updates are paper-tested only from timestamped quotes and events.", ("live.events", "markets.live"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_EXCHANGE_001", "Exchange execution", "REG", "market", "EXCHANGE",
           "Bid/ask and available size determine executable edge, not a displayed midpoint.", ("markets.exchange.orderbook",), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_FUTURES_001", "Futures calibration", "REG", "elo", "FUTURES",
           "Season futures are evaluated with time-to-settlement and observed liquidity.", ("markets.futures",), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_NRFI_001", "No-run first inning", "REG", "elo", "NRFI",
           "Starter and lineup run probabilities price a verified no-run-first-inning market.", ("starting_pitchers", "lineups", "markets.inning"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_YRFI_001", "Run first inning", "REG", "elo", "YRFI",
           "A verified first-inning run distribution is compared with the observed price.", ("starting_pitchers", "lineups", "markets.inning"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_INNING_001", "Inning and period markets", "REG", "elo", "INNING",
           "Period-specific scoring expectations are tested without borrowing full-game outcomes.", ("pitch_level", "markets.inning"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_PREDICTION_001", "Prediction-market execution", "REG", "market", "PREDICTION_MARKET",
           "Contract probability is compared with observed bid/ask, size, fees and settlement rules.", ("markets.prediction_market",), status="DATA_UNAVAILABLE"),
        # ---- 2026-09-22 catalog expansion (v1 hypotheses, appended; existing
        # entries are never edited in place — see versioning contract).
        _s("MLB_REG_ELO_STRICT_001", "Elo strict-entry variant", "REG", "elo", "ML",
           "A higher minimum-edge gate on the Elo baseline trades volume for entry quality.",
           ("games.scores",), min_edge=0.035,
           notes="v1 — lineage: MLB_REG_ELO_001 with the entry threshold tightened to 3.5%; pending the next chronological backtest."),
        _s("MLB_REG_FORM_MILD_001", "Thirty-game form, mild entry", "REG", "form", "ML",
           "A 1% minimum-edge gate tests whether form signal survives at higher volume.",
           ("games.scores",), min_edge=0.01,
           notes="v1 — lineage: MLB_REG_FORM_001 with the entry threshold relaxed to 1%; pending the next chronological backtest."),
        _s("MLB_REG_DOUBLEHEADER_001", "Doubleheader second-game fatigue", "REG", "elo", "ML",
           "Second games of doubleheaders may carry a measurable fatigue/rotation effect.",
           ("schedule", "games.doubleheader_flags"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_INTERLEAGUE_001", "Interleague unfamiliarity", "REG", "elo", "ML",
           "Rare cross-league matchups could weaken team-strength priors; measured, not assumed.",
           ("games.scores", "schedule.league_alignment"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_DAYNIGHT_001", "Day-after-night turnaround", "REG", "elo", "ML",
           "A night game followed by a day game tests short-turnaround performance drag.",
           ("schedule.start_times", "games.scores"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_TRAVEL_TZ_001", "Time-zone travel load", "REG", "elo", "ML",
           "Multi-zone trips with short rest add to the rest differential.",
           ("travel_rest", "venues.timezones"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_STARTER_FIP_001", "Starter FIP prior", "REG", "elo", "ML",
           "Point-in-time starter FIP adjustments to the team prior.",
           ("starting_pitchers", "statistics.pitching.fip"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_XWOBA_LINEUP_001", "Lineup xwOBA quality", "REG", "elo", "ML",
           "Announced-lineup expected contact quality adjusts the pre-game estimate.",
           ("lineups", "statcast.xwoba"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_BULLPEN_QUALITY_001", "Bullpen quality-adjusted rest", "REG", "elo", "ML",
           "Which relievers are rested matters more than a raw workload count.",
           ("bullpen_usage", "statistics.pitching.reliever_quality"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_PARK_HR_001", "Park home-run factor totals", "REG", "poisson_total", "TOTAL",
           "Park-specific home-run factors shift the run distribution used for totals.",
           ("venues.park_factors", "statistics.home_runs"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_WEATHER_WIND_001", "Wind vector on totals", "REG", "poisson_total", "TOTAL",
           "Pre-game wind speed/direction at open-air parks moves expected scoring.",
           ("weather.forecast.wind", "venues.roof"), status="DATA_UNAVAILABLE"),
        _s("MLB_REG_CLINCH_REST_001", "Post-clinch resting pattern", "REG", "elo", "ML",
           "Clinched teams resting regulars in late September may underperform priors.",
           ("schedule", "standings.clinch_status"), status="DATA_UNAVAILABLE"),
    ]
    post: list[Strategy] = [
        _s("MLB_POST_XREG_001", "Regular-season transfer control", "POST", "xreg", "ML",
           "Control: apply the regular-season model to postseason without a postseason adjustment.", ("regular_model", "postseason.games")),
        _s("MLB_POST_ADJUSTED_001", "Regular plus postseason adjustment", "POST", "round_specific", "ML",
           "A postseason intercept learned only from prior postseason seasons improves transfer.", ("regular_model", "postseason.games")),
        _s("MLB_POST_HIERARCHICAL_001", "Hierarchical REG plus PO", "POST", "hierarchical", "ML",
           "Career/multi-year, current season, late season, prior postseason and current series evidence are partially pooled.", ("regular_model", "postseason.games", "series_state")),
        _s("MLB_POST_DEDICATED_001", "Dedicated postseason Elo", "POST", "post_elo", "ML",
           "A postseason-only Elo tracker inherits the regular-season rating as a prior and then updates only on earlier postseason games.",
           ("regular_model", "postseason.games")),
        _s("MLB_POST_SERIESSTATE_001", "Series-state model", "POST", "hierarchical", "ML",
           "Game number, record, elimination, clinching and games remaining add measurable information only if they do.", ("series_state", "postseason.games"), min_edge=0.03),
        _s("MLB_POST_PITCHING_001", "Postseason pitching leash", "POST", "hierarchical", "ML",
           "Shorter leashes, starter workload and pitch mix are tested as postseason-specific features.", ("postseason.pitching",), status="DATA_UNAVAILABLE"),
        _s("MLB_POST_BULLPEN_001", "Postseason bullpen workload", "POST", "bullpen", "ML",
           "Postseason bullpen workload (play-by-play derived, point-in-time) uses its "
           "own online coefficient so the small postseason sample cannot borrow the "
           "regular-season fit silently.", ("postseason.games", "bullpen_usage")),
        _s("MLB_POST_LINEUP_001", "Postseason lineup construction", "POST", "hierarchical", "ML",
           "Platoons, pinch hitting and defensive substitutions are measured in postseason only.", ("postseason.lineups",), status="DATA_UNAVAILABLE"),
        _s("MLB_POST_MANAGER_001", "Postseason managerial decisions", "POST", "hierarchical", "ML",
           "Managerial hook and substitution patterns are estimated without assuming a direction.", ("postseason.managerial",), status="DATA_UNAVAILABLE"),
        _s("MLB_POST_MARKET_001", "Postseason market model", "POST", "market", "ML",
           "The de-vigged verified postseason price is compared with model probability "
           "only where an open/close quote exists; postseason market efficiency is "
           "measured against postseason rows, never inferred from regular-season prices.",
           ("postseason.markets",)),
    ]
    for round_code, label in (("WC", "Wild Card"), ("DS", "Division Series"),
                              ("LCS", "League Championship Series"), ("WS", "World Series")):
        post.extend([
            _s(f"MLB_POST_{round_code}_ELO_001", f"{label} round-specific Elo", round_code, "round_specific", "ML",
               f"Dedicated {label} intercept/parameters learned only from earlier {label} seasons.", ("postseason.games", "regular_model")),
            _s(f"MLB_POST_{round_code}_HIER_001", f"{label} hierarchical model", round_code, "hierarchical", "ML",
               f"Partial pooling for {label} prevents overreaction to its small sample.", ("postseason.games", "series_state", "regular_model")),
            _s(f"MLB_POST_{round_code}_STATE_001", f"{label} series-state model", round_code, "hierarchical", "ML",
               f"{label} game number, home status and elimination state are evaluated point-in-time.", ("series_state",)),
            _s(f"MLB_POST_{round_code}_MARKET_001", f"{label} market model", round_code, "market", "ML",
               f"{label} market efficiency is measured from observed verified prices, never inferred from outcomes.",
               ("postseason.markets",)),
        ])
    # ---- 2026-09-22 postseason catalog expansion (appended v1 hypotheses).
    post.extend([
        _s("MLB_POST_MOMENTUM_001", "Series momentum update", "POST", "hierarchical", "ML",
           "Reversal-vs-momentum in series is measured from earlier games only; no direction is assumed.",
           ("series_state", "postseason.games"),
           notes="v1 — defined 2026-09-22; pending the next chronological backtest."),
        _s("MLB_POST_SHORTREST_ACE_001", "Ace on short rest", "POST", "elo", "ML",
           "Starters on three days' rest change pre-game probability; requires verified usage records.",
           ("postseason.pitching", "starting_pitchers"), status="DATA_UNAVAILABLE"),
        _s("MLB_POST_CLOSER_AVAIL_001", "Closer availability", "POST", "elo", "ML",
           "Back-to-back closer usage across series games affects late-inning win probability.",
           ("bullpen_usage", "statistics.leverage"), status="DATA_UNAVAILABLE"),
        _s("MLB_POST_TRAVEL_001", "Postseason travel and workout gaps", "POST", "elo", "ML",
           "Cross-series travel days and optional-workout gaps are tracked as schedule features.",
           ("schedule", "travel_rest"), status="DATA_UNAVAILABLE"),
        _s("MLB_POST_WC_MOMENTUM_001", "Wild Card momentum check", "WC", "hierarchical", "ML",
           "Game-1 result effects within the short best-of-three are measured point-in-time.",
           ("series_state",),
           notes="v1 — defined 2026-09-22; pending the next chronological backtest."),
        _s("MLB_POST_DS_MOMENTUM_001", "Division Series momentum check", "DS", "hierarchical", "ML",
           "Game-to-game series state effects inside best-of-five are measured point-in-time.",
           ("series_state",),
           notes="v1 — defined 2026-09-22; pending the next chronological backtest."),
        _s("MLB_POST_LCS_MOMENTUM_001", "LCS momentum check", "LCS", "hierarchical", "ML",
           "Best-of-seven series state effects are measured point-in-time.",
           ("series_state",),
           notes="v1 — defined 2026-09-22; pending the next chronological backtest."),
        _s("MLB_POST_WS_MOMENTUM_001", "World Series momentum check", "WS", "hierarchical", "ML",
           "Fall Classic series state effects are measured point-in-time.",
           ("series_state",),
           notes="v1 — defined 2026-09-22; pending the next chronological backtest."),
    ])
    # Experiment aliases are catalog labels for the permanent A–E comparison.
    # They must bind to the SAME model function as the named strategy they
    # represent.  They are excluded from environment PnL roll-ups so they
    # cannot double-count the underlying strategy.
    for exp_id, name, model, parent in (
            ("A", "Model A transfer", "xreg", "MLB_POST_XREG_001"),
            ("B", "Model B adjusted", "round_specific", "MLB_POST_ADJUSTED_001"),
            ("C", "Model C dedicated", "post_elo", "MLB_POST_DEDICATED_001"),
            ("D", "Model D round-specific", "round_specific", "MLB_POST_ADJUSTED_001"),
            ("E", "Model E hierarchical", "hierarchical", "MLB_POST_HIERARCHICAL_001")):
        post.append(_s(f"MLB_POST_MODEL_{exp_id}_001", name, "POST", model, "ML",
                       f"Permanent comparison experiment {exp_id} (alias of {parent}); not declared superior in advance.",
                       ("postseason.games", "regular_model"),
                       notes=f"ALIAS_OF={parent}; excluded from unique environment totals.",
                       extra={"alias_of": parent, "experiment": exp_id}))
    return reg + post


def special_rules(sid: str, g, f, ctx, decision):
    if decision is None:
        return None
    if sid == "MLB_REG_FAV_BIAS_001":
        # Entry requires a timestamped, verified quote.  Selecting the
        # favorite from an untimestamped (closing) price column would let
        # information that may not have been available at decision time
        # choose the side — a look-ahead gate, not a strategy.
        if not f.get("market_quote_verified", False):
            return None
        ho, ao = f.get("home_odds"), f.get("away_odds")
        if not (_num(ho) and _num(ao)):
            return None
        favorite_home = float(ho) < float(ao)
        favorite_price = float(ho if favorite_home else ao)
        if favorite_price > float(ctx.get("fav_max", -150.0)):
            return None
        selection = "HOME" if favorite_home else "AWAY"
        p = decision["p_model"] if selection == decision["selection"] else 1.0 - decision["p_model"]
        return {**decision, "selection": selection, "p_model": p,
                "fair_price": p, "required_price": prob_to_am(p)}
    if sid == "MLB_POST_SERIESSTATE_001" or sid.endswith("_STATE_001"):
        if not (f.get("elimination_a") or f.get("elimination_b") or
                f.get("clinch_a") or f.get("clinch_b")):
            return None
    return decision


if __name__ == "__main__":
    import json
    print(json.dumps([s.to_record() for s in build_catalog()], indent=2, default=str))
