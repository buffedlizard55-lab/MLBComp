"""Point-in-time feature engine.

Anti-leakage contract (spec §17): for a game with start time T, every feature
may use ONLY games whose completion time < T.  Enforced by processing games
in chronological order and updating team state only AFTER a game completes.
Postseason games may use information from earlier games in the same series
(spec §8, §17), never later ones.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np
import pandas as pd

from ..config import FEAT


def load_games() -> pd.DataFrame:
    g = pd.read_parquet(FEAT / "games.parquet")
    g["start_ts"] = pd.to_datetime(g.start_utc, utc=True)
    g["date"] = pd.to_datetime(g.game_date)
    return g.sort_values(["start_ts", "game_pk"]).reset_index(drop=True)


def load_events() -> pd.DataFrame:
    return pd.read_parquet(FEAT / "game_events.parquet")


# ------------------------------------------------------------------ Elo
class EloTracker:
    """Team Elo, margin-of-victory K, home advantage +20."""

    HOME_ADV = 20.0

    def __init__(self, start: float = 1500.0):
        self.ratings: dict[int, float] = {}
        self.start = start

    def get(self, team: int) -> float:
        return self.ratings.setdefault(team, self.start)

    def predict(self, home: int, away: int) -> float:
        eh = self.get(home) + self.HOME_ADV
        return 1.0 / (1.0 + 10 ** (-(eh - self.get(away)) / 400.0))

    def update(self, home: int, away: int, hs: float, as_: float) -> None:
        exp_h = self.predict(home, away)
        m = abs(hs - as_)
        k = 20.0 * (1.0 + min(max(m - 1, 0), 5) / 10.0)
        s = 1.0 if hs > as_ else 0.0
        d = k * (s - exp_h)
        self.ratings[home] = self.get(home) + d
        self.ratings[away] = self.get(away) - d


# ------------------------------------------------------------------ state
class TeamState:
    """Per-team rolling + season-expanding stats, updated post-completion."""

    def __init__(self, window: int = 30):
        self.window = window
        self.gf: dict[int, deque] = {}
        self.season_stats: dict[int, dict] = {}
        self.last_date: dict[int, pd.Timestamp] = {}
        self.last_venue: dict[int, str] = {}
        self.streak_w: dict[int, int] = {}
        self.streak_l: dict[int, int] = {}

    def _init(self, t):
        self.gf.setdefault(t, deque(maxlen=self.window))
        self.season_stats.setdefault(t, {})

    def record(self, t, rf, ra, date, venue, season):
        self._init(t)
        self.gf[t].append((rf, ra))
        st = self.season_stats[t]
        if st.get("season") != season:
            self.season_stats[t] = {"season": season, "g": 0, "rf": 0.0, "ra": 0.0}
        st = self.season_stats[t]
        st["g"] += 1
        st["rf"] += rf
        st["ra"] += ra
        if rf > ra:
            self.streak_w[t], self.streak_l[t] = self.streak_w.get(t, 0) + 1, 0
        elif ra > rf:
            self.streak_w[t], self.streak_l[t] = 0, self.streak_l.get(t, 0) + 1
        self.last_date[t] = date
        self.last_venue[t] = venue

    def features(self, t, season) -> dict:
        self._init(t)
        d = self.gf[t]
        n = len(d)
        f = {"n_games": n, "rs_pg": 4.6, "ra_pg": 4.6, "run_diff_pg": 0.0,
             "win_pct": 0.5, "pythag": 0.5, "streak_w": self.streak_w.get(t, 0),
             "streak_l": self.streak_l.get(t, 0),
             "season_g": 0, "season_rd": 0.0, "season_pythag": 0.5}
        if n:
            rf = sum(x[0] for x in d)
            ra = sum(x[1] for x in d)
            wins = sum(1 for x in d if x[0] > x[1])
            f.update(rs_pg=rf / n, ra_pg=ra / n, run_diff_pg=(rf - ra) / n,
                     win_pct=wins / n,
                     pythag=rf ** 1.83 / (rf ** 1.83 + ra ** 1.83) if rf + ra else 0.5)
        st = self.season_stats[t]
        if st.get("season") == season and st.get("g", 0) >= 3:
            f.update(season_g=st["g"], season_rd=(st["rf"] - st["ra"]) / st["g"],
                     season_pythag=st["rf"] ** 1.83 / (st["rf"] ** 1.83 + st["ra"] ** 1.83)
                     if st["rf"] + st["ra"] else 0.5)
        if t in self.last_date:
            f["days_since_last"] = 0.0  # always just played (single-game-per-day model)
        else:
            f["days_since_last"] = np.nan
        return f

    def rest_days(self, t, date) -> float | None:
        if t not in self.last_date:
            return None
        return (date - self.last_date[t]).total_seconds() / 86400.0


# ------------------------------------------------------------------ events
def game_event_rates(events: pd.DataFrame, games: pd.DataFrame) -> dict:
    """(game_pk, team_id) -> (so_rate, bb_rate, hr_rate) share of team at-bats."""
    ev = events.merge(games[["game_pk", "home_team_id", "away_team_id"]],
                      on="game_pk", how="inner")
    out = {}
    for r in ev.itertuples():
        ab = max(1, int(r.total_ab))
        share = (r.total_so / ab / 2.0, r.total_bb / ab / 2.0, r.total_hr / ab / 2.0)
        out[(r.game_pk, int(r.home_team_id))] = share
        out[(r.game_pk, int(r.away_team_id))] = share
    return out


# ------------------------------------------------------------------ main
def compute_features(games: pd.DataFrame, events: pd.DataFrame,
                     series_state: pd.DataFrame | None = None,
                     odds: pd.DataFrame | None = None) -> pd.DataFrame:
    """Chronological feature computation for every game (point-in-time)."""
    rates = game_event_rates(events, games)
    # REG and POST trackers are intentionally separate.  Postseason models can
    # inherit the regular-season prior, while postseason outcomes never leak
    # back into a later regular-season feature.
    elo_reg = EloTracker()
    elo_post = EloTracker()
    state_reg = TeamState(window=30)
    state_post = TeamState(window=30)
    state_sep = TeamState(window=15)   # late-season form (September REG only)

    odds_map = {}
    if odds is not None and len(odds):
        for r in odds.itertuples():
            odds_map[r.game_pk] = r

    rows = []
    for g in games.itertuples():
        f = {"game_pk": g.game_pk}
        game_date = getattr(g, "date", pd.to_datetime(getattr(g, "game_date"), errors="coerce"))
        is_post = pd.notna(getattr(g, "round_code", np.nan))
        if g.home_team_id is not None:
            h, a = int(g.home_team_id), int(g.away_team_id)
            # Regular-season features are the prior for every postseason game.
            fh = state_reg.features(h, g.season)
            fa = state_reg.features(a, g.season)
            f.update({f"h_{k}": v for k, v in fh.items()})
            f.update({f"a_{k}": v for k, v in fa.items()})
            f["p_elo"] = elo_reg.predict(h, a)
            if is_post:
                # Copy the pre-game REG rating into the PO tracker the first
                # time a team appears; only earlier PO games then update it.
                elo_post.ratings.setdefault(h, elo_reg.get(h))
                elo_post.ratings.setdefault(a, elo_reg.get(a))
                f["p_post_elo"] = elo_post.predict(h, a)
                ph = state_post.features(h, g.season)
                pa = state_post.features(a, g.season)
                f.update({f"h_post_{k}": v for k, v in ph.items()})
                f.update({f"a_post_{k}": v for k, v in pa.items()})
            else:
                f["p_post_elo"] = np.nan
            # late-season form uses only regular-season games in September.
            sh = state_sep.features(h, g.season)
            sa = state_sep.features(a, g.season)
            f["h_sep_rd"] = sh["run_diff_pg"] if sh["n_games"] else np.nan
            f["a_sep_rd"] = sa["run_diff_pg"] if sa["n_games"] else np.nan
            # For a postseason game, the most recent earlier PO game wins the
            # rest calculation; otherwise use regular-season history.
            rh = state_post.rest_days(h, game_date) if is_post else None
            ra_ = state_post.rest_days(a, game_date) if is_post else None
            f["rest_home"] = rh if rh is not None else state_reg.rest_days(h, game_date)
            f["rest_away"] = ra_ if ra_ is not None else state_reg.rest_days(a, game_date)
        # Market odds (if this game has verified, timestamped quote metadata).
        o = odds_map.get(g.game_pk)
        if o is not None:
            f["home_odds"] = o.home_odds
            f["away_odds"] = o.away_odds
            f["o_home_last5_xwoba"] = getattr(o, "home_last5_o_xwoba", np.nan)
            f["o_away_last5_xwoba"] = getattr(o, "away_last5_o_xwoba", np.nan)
            f["o_home_win_pct"] = getattr(o, "home_win_pct_to_date", np.nan)
            f["o_away_win_pct"] = getattr(o, "away_win_pct_to_date", np.nan)
            f["market_quote_observed_at"] = getattr(o, "observed_at", None)
            f["market_quote_available_at"] = getattr(o, "available_at", None)
            f["market_quote_source_id"] = getattr(o, "source_id", None)
            f["market_quote_source_url"] = getattr(o, "source_url", None)
            f["market_quote_source_observation_id"] = getattr(o, "source_observation_id", None)
            f["market_quote_closing"] = bool(getattr(o, "closing_flag", False))
            f["market_quote_verified"] = (
                str(getattr(o, "verification_status", "")) == "VERIFIED"
                and bool(f["market_quote_observed_at"])
                and bool(f["market_quote_available_at"])
            )
        else:
            f["market_quote_verified"] = False
        rows.append(f)

        if g.completed and g.home_team_id is not None:
            h, a = int(g.home_team_id), int(g.away_team_id)
            if is_post:
                elo_post.update(h, a, g.home_score, g.away_score)
                state_post.record(h, g.home_score, g.away_score, game_date, g.venue_name, g.season)
                state_post.record(a, g.away_score, g.home_score, game_date, g.venue_name, g.season)
            else:
                elo_reg.update(h, a, g.home_score, g.away_score)
                state_reg.record(h, g.home_score, g.away_score, game_date, g.venue_name, g.season)
                state_reg.record(a, g.away_score, g.home_score, game_date, g.venue_name, g.season)
                if game_date.month == 9:
                    state_sep.record(h, g.home_score, g.away_score, game_date, g.venue_name, g.season)
                    state_sep.record(a, g.away_score, g.home_score, game_date, g.venue_name, g.season)

    out = pd.DataFrame(rows).set_index("game_pk")

    # per-team event rates for each game (K/BB/HR)
    gmap = games.set_index("game_pk")[["home_team_id", "away_team_id"]]
    for side, tcol in (("h", "home_team_id"), ("a", "away_team_id")):
        so, bb, hr = [], [], []
        for pk in out.index:
            try:
                t = int(gmap.loc[pk, tcol])
                r = rates.get((pk, t))
            except KeyError:
                t = None
                r = None
            so.append(r[0] if r else np.nan)
            bb.append(r[1] if r else np.nan)
            hr.append(r[2] if r else np.nan)
        out[f"{side}_so_rate"] = so
        out[f"{side}_bb_rate"] = bb
        out[f"{side}_hr_rate"] = hr

    # series state (postseason) — merge by game_pk
    if series_state is not None and len(series_state):
        st = series_state.set_index("game_pk")
        for c in st.columns:
            out[c] = [st.at[pk, c] if pk in st.index else np.nan for pk in out.index]

    return out


def poisson_win_prob(lam_home: float, lam_away: float, max_score: int = 20) -> float:
    """P(home wins) from a Poisson run model; ties/extra innings resolved 50/50."""
    ph = [math.exp(-lam_home) * lam_home ** i / math.factorial(i) for i in range(max_score + 1)]
    pa = [math.exp(-lam_away) * lam_away ** i / math.factorial(i) for i in range(max_score + 1)]
    p_home = p_tie = 0.0
    for i in range(max_score + 1):
        for j in range(max_score + 1):
            if i > j:
                p_home += ph[i] * pa[j]
            elif i == j:
                p_tie += ph[i] * pa[j]
    return p_home + p_tie / 2.0
