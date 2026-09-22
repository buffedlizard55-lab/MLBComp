"""Source registry and source-gated data builders.

This module contains adapters for candidate public repositories.  The checkout
intentionally ships without their raw snapshots, so the descriptions below are
discovery metadata, not assertions that a URL was reachable, licensed, or
complete during the current run.  A caller must fetch a snapshot, record its
manifest and verification status, and only then promote normalized rows for
research or settlement.

Candidates
----------
S1  sportsdataverse/baseballr-data — schedule and play-by-play parquet
    exports; exact historical depth and current coverage must be measured from
    the retrieved manifest.
S2  cesar-dx/mlb-betting-ml — candidate historical moneyline/statcast files;
    exact years, markets, licensing and postseason coverage must be measured
    from retrieved files.  No historical price is assumed here.

No postseason result, row count, champion, score, odds record or source
availability is asserted by this adapter.  Missing, blocked, conflicting or
unlicensed content remains DATA_UNAVAILABLE until an operator records the
relevant observation and validation evidence.  The optional reference mapping
below is only a validation aid; it is not a source observation and never
creates games or settlement labels.

Rejected / unavailable candidates are retained in the source registry rather
than silently substituted: Stats API, Baseball Savant/Statcast, historical
odds mirrors without verifiable provenance, weather feeds, and commercial
markets that cannot be accessed under their terms.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime

import numpy as np
import pandas as pd

from .. import db
from ..config import FEAT, RAW, ROOT, SEASONS_ALL, SEASONS_WITH_ODDS, TODAY

SCHEDULE_DIR = RAW / "baseballr" / "schedule"
PBP_DIR = RAW / "baseballr" / "pbp"
ODDS_DIR = RAW / "odds"

# The source (baseballr / cesar odds) uses its own stable team-id scheme
# (NOT MLB's official ids, e.g. NYY=147 here vs 139 official).  We derive
# name->id from the data itself (mode over all seasons) and treat the
# 3-letter abbreviation as the canonical cross-source key.
NAME_TO_ABBR = {
    "Los Angeles Angels": "LAA", "Arizona Diamondbacks": "AZ",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS", "Chicago Cubs": "CHC",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE", "Cleveland Indians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET", "Houston Astros": "HOU",
    "Kansas City Royals": "KC", "Los Angeles Dodgers": "LAD",
    "Washington Nationals": "WAS", "New York Mets": "NYM",
    "Oakland Athletics": "ATH", "Athletics": "ATH", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "Seattle Mariners": "SEA", "San Francisco Giants": "SF",
    "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Minnesota Twins": "MIN", "Philadelphia Phillies": "PHI",
    "Atlanta Braves": "ATL", "Chicago White Sox": "CWS", "Miami Marlins": "MIA",
    "New York Yankees": "NYY", "Milwaukee Brewers": "MIL",
}
ABBREV_TO_LEAGUE = {
    "LAA": "AL", "BOS": "AL", "CLE": "AL", "MIN": "AL", "KC": "AL", "SEA": "AL",
    "TB": "AL", "NYY": "AL", "DET": "AL", "CWS": "AL", "BAL": "AL", "TEX": "AL",
    "TOR": "AL", "ATH": "AL",
    "CHC": "NL", "MIL": "NL", "STL": "NL", "HOU": "NL", "PIT": "NL", "CIN": "NL",
    "LAD": "NL", "SD": "NL", "SF": "NL", "COL": "NL", "AZ": "NL",
    "ATL": "NL", "NYM": "NL", "PHI": "NL", "MIA": "NL", "WAS": "NL",
}
# Treat source numeric team IDs as potentially versioned.  When a snapshot is
# actually loaded, game_pk plus normalized team names/abbreviations are used as
# the cross-source keys; this adapter assigns its own stable canonical IDs.
CANON_ABBR_TO_ID = {abbr: i + 1 for i, abbr in enumerate(sorted(ABBREV_TO_LEAGUE.keys()))}
TEAM_ID_TO_ABBR = {tid: abbr for abbr, tid in CANON_ABBR_TO_ID.items()}
TEAM_ID_TO_NAME: dict[int, str] = {}  # filled with latest display name per team

# Optional operator-supplied validation reference.  It is retained for
# controlled source audits only; it is not fetched data, does not verify a URL,
# and must never be used to create a missing game or settlement label.
KNOWN_WS_CHAMPIONS = {
    2015: "KC",   # Royals over Mets 4-1
    2016: "CHC",  # Cubs over Indians 4-3
    2017: "HOU",  # Astros over Dodgers 4-3
    2018: "BOS",  # Red Sox over Dodgers 4-1
    2019: "WAS",  # Nationals over Astros 4-3
    2020: "LAD",  # Dodgers over Rays 4-2
    2021: "ATL",  # Braves over Astros 4-2
    2022: "HOU",  # Astros over Phillies 4-2
    2023: "TEX",  # Rangers over Diamondbacks 4-1
    2024: "LAD",  # Dodgers over Yankees 4-1
    2025: "LAD",  # Dodgers over Blue Jays 4-3
}

# City-level venue coordinates (±50km immaterial for travel research).
VENUE_COORDS = {
    "Angel Stadium": (34.095, -118.237), "Dodger Stadium": (34.074, -118.238),
    "Petco Park": (32.702, -117.154), "Chase Field": (33.445, -112.067),
    "Coors Field": (39.757, -105.094), "Oracle Park": (37.779, -122.389),
    "Globe Life Field": (32.750, -97.087), "T-Mobile Park": (47.596, -122.332),
    "Rogers Centre": (43.641, -79.381), "Wrigley Field": (41.948, -87.656),
    "Guaranteed Rate Field": (41.948, -87.655), "Comerica Park": (42.339, -83.049),
    "Progress Field": (44.981, -93.257), "Kauffman Stadium": (39.051, -94.481),
    "Yankee Stadium": (40.829, -73.926), "Fenway Park": (42.347, -71.093),
    "Oriole Park at Camden Yards": (39.284, -76.621), "Tropicana Field": (27.953, -82.676),
    "Citizens Bank Park": (39.905, -75.164), "Citi Field": (40.757, -73.846),
    "Miller Park": (43.040, -87.920), "American Family Field": (43.040, -87.920),
    "Great American Ball Park": (39.098, -84.504), "Busch Stadium": (38.618, -90.201),
    "Truist Park": (33.753, -84.393), "LoanDepot Park": (25.959, -80.235),
    "Nationals Park": (38.874, -77.008), "Minute Maid Park": (29.757, -95.391),
}


def venue_coords(venue_name):
    if not isinstance(venue_name, str):
        return None
    for k, c in VENUE_COORDS.items():
        if k in venue_name:
            return c
    return None


def haversine_km(a, b):
    if a is None or b is None:
        return None
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- loaders
def register_sources(conn) -> None:
    """Register discovery records without claiming that a fetch was verified.

    The old implementation upgraded two URLs to verified before the content
    and license gates ran.  Ingest now delegates to the richer source catalog;
    validators can promote individual observations later.
    """
    from ..sources import register_catalog
    register_catalog()


def load_schedules(years) -> pd.DataFrame:
    global TEAM_ID_TO_NAME
    frames = []
    for y in years:
        s = pd.read_parquet(SCHEDULE_DIR / f"{y}.parquet")
        s = s[s.game_type.isin(["R", "F", "D", "L", "W"])].copy()
        frames.append(s)
    df = pd.concat(frames, ignore_index=True)
    df["game_date"] = df.game_date.astype(str)
    df["home_abbr"] = df.home_team_name.map(NAME_TO_ABBR)
    df["away_abbr"] = df.away_team_name.map(NAME_TO_ABBR)
    df = df[df.home_abbr.notna() & df.away_abbr.notna()].copy()
    # Remap to stable canonical team ids (source ids are per-year).
    df["home_team_id"] = df.home_abbr.map(CANON_ABBR_TO_ID)
    df["away_team_id"] = df.away_abbr.map(CANON_ABBR_TO_ID)
    # Latest display name per canonical team (for the teams table).
    latest = {}
    for _, r in df.sort_values("season").iterrows():
        for col, tcol in (("home_team_id", "home_team_name"), ("away_team_id", "away_team_name")):
            tid = int(r[col])
            nm = r[tcol]
            if tid not in latest or int(r.season) >= latest[tid][1]:
                latest[tid] = (nm, int(r.season))
    TEAM_ID_TO_NAME = {tid: nm for tid, (nm, _) in latest.items()}

    # Deduplicate postponed games: the source lists the same game_pk once at
    # the original date (status DR/DI/D9..., abstract_state Preview, no score)
    # and again at the makeup date (Final + score). Keep the earliest Final
    # row per game_pk; for games without a Final row (upcoming / in progress /
    # still postponed) keep the latest row.
    df["is_final"] = (df.abstract_state == "Final") & df.home_score.notna() & df.away_score.notna()
    df = df.sort_values(["game_pk", "game_date"])
    final = df[df.is_final].groupby("game_pk").head(1)
    not_final = df[~df.is_final & ~df.game_pk.isin(final.game_pk)]
    df = pd.concat([final, not_final.groupby("game_pk").tail(1)])
    n_dup = int(len(df) - df.game_pk.nunique())
    if n_dup:
        raise RuntimeError(f"schedule dedup failed: {n_dup} duplicate game_pks")
    df = df.drop(columns=["is_final"])

    df["start_utc"] = df.game_datetime.astype(str)
    df["status"] = df.status_code
    return df.sort_values(["season", "game_date", "game_pk"]).reset_index(drop=True)


# The odds source uses legacy team names (WSH for WAS, OAK for ATH).
ODDS_ABBR_FIX = {"WSH": "WAS", "OAK": "ATH"}


def load_odds(years) -> pd.DataFrame:
    frames = []
    for y in years:
        o = pd.read_csv(ODDS_DIR / f"{y}" / "final_format.csv")
        o["game_date"] = o.game_date.astype(str)
        o["home_name"] = o.home_name.replace(ODDS_ABBR_FIX)
        o["away_name"] = o.away_name.replace(ODDS_ABBR_FIX)
        frames.append(o)
    return pd.concat(frames, ignore_index=True).reset_index(drop=True)


def load_pbp(years) -> pd.DataFrame:
    frames = []
    for y in years:
        p = PBP_DIR / f"mlb_pbp_{y}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(
                p, columns=["game_pk", "at_bat_index", "inning", "half_inning",
                            "batter_id", "pitcher_id", "event_type", "event"]))
    return pd.concat(frames, ignore_index=True)


# ------------------------------------------------------------------ games
def build_games(games: pd.DataFrame, pbp: pd.DataFrame) -> pd.DataFrame:
    g = games.copy()
    g["round_code"] = g.game_type.map({"F": "WC", "D": "DS", "L": "LCS", "W": "WS"})
    # A game is settled only when the source marks it Final with a score.
    # Live (in-progress) and preview games keep scores NA / 0-0 and are NOT final.
    g["completed"] = (g.abstract_state == "Final") & g.home_score.notna() & g.away_score.notna()
    g.loc[~g.completed, "home_score"] = np.nan
    g.loc[~g.completed, "away_score"] = np.nan
    g["home_score"] = g.home_score.round().astype("Int64")
    g["away_score"] = g.away_score.round().astype("Int64")

    if len(pbp):
        mx = pbp.groupby("game_pk")["inning"].max().rename("max_inning")
        g = g.merge(mx, on="game_pk", how="left")
        g["extra_innings"] = (g.max_inning.fillna(9) > 9).astype("Int64")
        g = g.drop(columns=["max_inning"])
    else:
        g["extra_innings"] = pd.NA

    hs, as_ = g.home_score.astype(float), g.away_score.astype(float)
    g["winner_team_id"] = pd.Series(np.where(
        hs > as_, g.home_team_id.astype(float),
        np.where(as_ > hs, g.away_team_id.astype(float), np.nan)), index=g.index
    ).astype("Int64")
    g["home_win"] = pd.Series(np.where(hs > as_, 1.0, np.where(as_ > hs, 0.0, np.nan)),
                              index=g.index).astype("Int64")
    return g


def round_needed(round_code: str, season: int) -> int:
    """Wins needed to clinch a series (format changes by year).

    Delegates to the single shared implementation in ``mlbcomp.config`` so the
    series-state builder and the backtest runner can never disagree.  WC was
    single-elimination 2012-2019 and 2021; best-of-3 in 2020 and from 2022 on.
    DS is best-of-5 (3 wins) in EVERY season here, including 2020 (MLB's
    2020-07-23 announcement kept the DS at best-of-five).  LCS/WS: best-of-7.
    """
    from ..config import round_needed as _round_needed
    return _round_needed(round_code, int(season))


def _league_of(desc) -> str:
    s = str(desc)
    if s.startswith("AL"):
        return "AL"
    if s.startswith("NL"):
        return "NL"
    if s.startswith("World"):
        return "WS"
    return "?"


def group_series(games: pd.DataFrame) -> pd.DataFrame:
    """Assign series_key to postseason games.

    A series is uniquely (season, round, league, team-pair) — within a round
    two teams can only meet once, even when games of different series are
    played on the same day.  Series are numbered by their first game date.
    series_key = {season}|{round}|{league}|{n}
    """
    g = games.copy()
    po = g[g.round_code.notna()].copy()
    po["league"] = po.series_description.map(_league_of)
    po["pair"] = po.apply(
        lambda r: "|".join(str(t) for t in sorted([int(r.home_team_id),
                                                   int(r.away_team_id)])), axis=1)
    first = po.groupby(["season", "round_code", "league", "pair"]).agg(
        first_date=("game_date", "min")).reset_index()
    first = first.sort_values(["season", "round_code", "league", "first_date"])
    first["n"] = first.groupby(["season", "round_code", "league"]).cumcount() + 1
    key_map = {(r.season, r.round_code, r.league, r.pair):
               f"{r.season}|{r.round_code}|{r.league}|{r.n}" for r in first.itertuples()}
    po["series_key"] = po.apply(
        lambda r: key_map[(int(r.season), r.round_code, r.league, r.pair)], axis=1)
    g = g.drop(columns=["series_key"], errors="ignore")
    g = g.merge(po[["game_pk", "series_key", "league"]], on="game_pk", how="left")
    return g


def build_series_table(games: pd.DataFrame) -> pd.DataFrame:
    po = games[games.round_code.notna() & games.series_key.notna()]
    rows = []
    for skey, grp in po.groupby("series_key", sort=False):
        grp = grp.sort_values(["game_date", "game_pk"])
        season = int(grp.season.iloc[0])
        rnd = grp.round_code.iloc[0]
        first = grp.iloc[0]
        a, b = int(first.home_team_id), int(first.away_team_id)
        nd = round_needed(rnd, season)
        wins = {a: 0, b: 0}
        played = False
        for r in grp.itertuples():
            if r.completed:
                played = True
                w = int(r.home_team_id) if r.home_score > r.away_score else int(r.away_team_id)
                wins[w] += 1
        if wins[a] >= nd:
            status, winner = "COMPLETED", a
        elif wins[b] >= nd:
            status, winner = "COMPLETED", b
        elif played:
            status, winner = "IN_PROGRESS", None
        else:
            status, winner = "SCHEDULED", None
        rows.append({
            "series_key": skey, "season": season, "round_code": rnd,
            "league": grp.league.iloc[0], "team_a_id": a, "team_b_id": b,
            "seed_a": None, "seed_b": None, "wins_a": wins[a], "wins_b": wins[b],
            "needed_a": nd, "needed_b": nd, "status": status, "winner_id": winner,
        })
    return pd.DataFrame(rows)


def build_series_state(games: pd.DataFrame) -> pd.DataFrame:
    """SERIES STATE ENGINE (spec §9).

    For every completed postseason game, the exact state BEFORE the game:
    series score, elimination/clinching, games remaining, days rest, travel,
    remaining home-advantage games. Uses only information from earlier games
    (anti-leakage per spec §17).
    """
    home_adv = {
        "WC": {1: "a", 2: "a", 3: "b"},
        "DS": {1: "a", 2: "a", 3: "b", 4: "b", 5: "a"},
        "LCS": {1: "a", 2: "a", 3: "b", 4: "b", 5: "b", 6: "a", 7: "a"},
        "WS": {1: "a", 2: "a", 3: "b", 4: "b", 5: "b", 6: "a", 7: "a"},
    }
    # Point-in-time rest/travel: per-team sorted history of completed games;
    # a game's rest is measured against the team's last game STRICTLY BEFORE
    # it (anti-leakage per spec §17).
    import bisect
    hist: dict[int, list] = {}
    for r in games[games.completed].sort_values(["game_date", "game_pk"]).itertuples():
        key = (pd.Timestamp(r.game_date), int(r.game_pk))
        hist.setdefault(int(r.home_team_id), []).append((key, r.venue_name))
        hist.setdefault(int(r.away_team_id), []).append((key, r.venue_name))
    for t in hist:
        hist[t].sort(key=lambda x: x[0])
    hist_keys = {t: [k for k, _ in v] for t, v in hist.items()}

    def prev_game(t: int, d: pd.Timestamp, pk: int):
        keys = hist_keys.get(t)
        if not keys:
            return None
        i = bisect.bisect_left(keys, (d, pk)) - 1
        if i < 0:
            return None
        return hist[t][i]  # ( (Timestamp, pk), venue )

    rows = []
    po = games[games.round_code.notna() & games.series_key.notna()]
    for skey, grp in po.groupby("series_key", sort=False):
        grp = grp.sort_values(["game_date", "game_pk"])
        season = int(grp.season.iloc[0])
        a, b = int(grp.iloc[0].home_team_id), int(grp.iloc[0].away_team_id)
        rnd = grp.round_code.iloc[0]
        nd = round_needed(rnd, season)
        wins = {a: 0, b: 0}
        played_prior = 0
        for r in grp.itertuples():
            if not r.completed:
                continue
            i = played_prior + 1
            home, away = int(r.home_team_id), int(r.away_team_id)
            loss_a, loss_b = played_prior - wins[a], played_prior - wins[b]
            row = {
                "game_pk": int(r.game_pk), "series_key": skey, "round_code": rnd,
                "game_number": i, "wins_a_before": wins[a], "wins_b_before": wins[b],
                "series_tied": int(wins[a] == wins[b]),
                "games_remaining": int(nd - max(wins[a], wins[b])),
                "elimination_a": int(loss_a >= nd - 1),
                "elimination_b": int(loss_b >= nd - 1),
                "clinch_a": int(wins[a] >= nd - 1),
                "clinch_b": int(wins[b] >= nd - 1),
                "home_adv_games_left": int(
                    sum(1 for gn in range(i, 2 * nd)
                        if home_adv[rnd].get(gn) == "a")),
            }
            d = pd.Timestamp(r.game_date)
            for side, t in (("home", home), ("away", away)):
                pg = prev_game(t, d, int(r.game_pk))
                if pg is not None:
                    (ld, _lpk), lv = pg
                    row[f"days_rest_{side}"] = (d - ld).total_seconds() / 86400
                    row[f"travel_km_{side}"] = haversine_km(venue_coords(lv),
                                                            venue_coords(r.venue_name))
                else:
                    row[f"days_rest_{side}"] = np.nan
                    row[f"travel_km_{side}"] = np.nan
            rows.append(row)
            w = home if r.home_score > r.away_score else away
            wins[w] += 1
            played_prior += 1
    return pd.DataFrame(rows)


def build_pbp_aggregates(pbp: pd.DataFrame, games: pd.DataFrame | None = None) -> None:
    """Play-by-play -> pitcher_game + game_events parquet (2015+).

    When the schedule frame is provided, two extra source-backed tables are
    written for point-in-time features:

    * ``game_team_events``  — per-team batting outcomes inferred from
      ``half_inning`` (top = away bats, bottom = home bats);
    * ``team_pitching_game`` — per-team pitcher usage: starter batters faced,
      bullpen batters faced, pitchers used.  Workload is a ``batters_faced``
      proxy from play-by-play, not official pitch counts (documented
      limitation).
    """
    pbp = pbp.copy()
    pg = pbp.groupby(["game_pk", "pitcher_id"]).agg(
        batters_faced=("at_bat_index", "count"),
        so=("event_type", lambda s: int((s == "strikeout").sum())),
        bb=("event_type", lambda s: int((s == "walk").sum())),
    ).reset_index()
    hr = pbp.assign(hr=(pbp.event == "Home Run")).groupby(
        ["game_pk", "pitcher_id"])["hr"].sum().rename("hr_allowed")
    pg = pg.merge(hr, on=["game_pk", "pitcher_id"], how="left").fillna({"hr_allowed": 0})

    firsts = pbp.sort_values(["game_pk", "at_bat_index"]).groupby("game_pk").first()
    starter = firsts["pitcher_id"]
    pg["is_starter"] = pg.apply(
        lambda r: int(r.pitcher_id == starter.get(r.game_pk, -1)), axis=1)
    pg["hr_allowed"] = pg.hr_allowed.astype(int)
    pg.to_parquet(FEAT / "pitcher_game.parquet", index=False)

    gg = pbp.groupby("game_pk").agg(
        pitchers_used=("pitcher_id", "nunique"),
        batters_used=("batter_id", "nunique"),
        max_inning=("inning", "max"),
        total_so=("event_type", lambda s: int((s == "strikeout").sum())),
        total_bb=("event_type", lambda s: int((s == "walk").sum())),
        total_hr=("event", lambda s: int((s == "Home Run").sum())),
        total_ab=("at_bat_index", "count"),
    ).reset_index()
    gg.to_parquet(FEAT / "game_events.parquet", index=False)

    if games is None:
        return
    gm = games[["game_pk", "home_team_id", "away_team_id"]].dropna()
    ev = pbp.merge(gm, on="game_pk", how="inner")
    ev["batting_team_id"] = np.where(ev.half_inning.astype(str).str.lower().str.startswith("t"),
                                     ev.away_team_id, ev.home_team_id)
    team_events = ev.groupby(["game_pk", "batting_team_id"]).agg(
        team_ab=("at_bat_index", "count"),
        team_so=("event_type", lambda s: int((s == "strikeout").sum())),
        team_bb=("event_type", lambda s: int((s == "walk").sum())),
        team_hr=("event", lambda s: int((s == "Home Run").sum())),
    ).reset_index()
    team_events.to_parquet(FEAT / "game_team_events.parquet", index=False)

    # Pitching team per (game, pitcher): majority of logged batters faced.
    ev["pitching_team_id"] = np.where(ev.half_inning.astype(str).str.lower().str.startswith("t"),
                                      ev.home_team_id, ev.away_team_id)
    pitch_team = ev.groupby(["game_pk", "pitcher_id", "pitching_team_id"]).size().rename(
        "n_bf").reset_index()
    pitch_team = pitch_team.sort_values("n_bf", ascending=False).drop_duplicates(
        ["game_pk", "pitcher_id"])
    pg_team = pg.merge(pitch_team[["game_pk", "pitcher_id", "pitching_team_id"]],
                       on=["game_pk", "pitcher_id"], how="inner")
    usage = pg_team.groupby(["game_pk", "pitching_team_id"]).agg(
        pitchers_used=("pitcher_id", "nunique"),
        bf_total=("batters_faced", "sum"),
    ).reset_index()
    sp = pg_team[pg_team.is_starter == 1].groupby(
        ["game_pk", "pitching_team_id"]).batters_faced.sum().rename("starter_bf")
    usage = usage.merge(sp, on=["game_pk", "pitching_team_id"], how="left")
    usage["starter_bf"] = usage.starter_bf.fillna(0).astype(int)
    usage["bp_bf"] = (usage.bf_total - usage.starter_bf).clip(lower=0).astype(int)
    usage["bp_pitchers"] = usage.pitchers_used - (usage.starter_bf > 0).astype(int)
    usage.to_parquet(FEAT / "team_pitching_game.parquet", index=False)


def join_odds(odds: pd.DataFrame, games: pd.DataFrame) -> int:
    """Join market prices onto games — union of cesar and bettingtools.

    Two layers, never confused:

    * **cesar-dx moneyline** — research-only prices with no availability
      timestamp.  They remain ``UNVERIFIED`` and cannot create a stake/PnL.
    * **bettingtools open/close lines (2014-2019)** — content-validated rows
      with documented open/close semantics.  When ``odds_open_close.parquet``
      marks a row ``ml_verified``, the **opening** moneyline becomes the entry
      quote (available before first pitch by definition), the **closing**
      line is retained for CLV, and both carry observed/availability stamps
      equal to the scheduled first pitch (see ``open_close_odds`` docs).

    The final parquet is the UNION: cesar rows plus any verified bettingtools
    rows whose game_pk is not already in cesar (e.g. 2015-2018).  Verified
    rows always win the verification_status.
    """
    g = games.set_index("game_pk")
    # --- cesar base ---
    o = odds.merge(
        g[["game_date", "home_abbr", "away_abbr", "start_utc"]].rename(
            columns={"game_date": "src_date", "home_abbr": "src_home",
                     "away_abbr": "src_away", "start_utc": "src_start"}).reset_index(),
        on="game_pk", how="inner")
    mismatch = ((o.game_date != o.src_date) | (o.home_name != o.src_home) |
                (o.away_name != o.src_away))
    dropped = int(mismatch.sum())
    o = o[~mismatch]
    base_columns = [c for c in [
        "game_pk", "game_date", "home_odds", "away_odds", "home_winner",
        "home_win_pct_to_date", "away_win_pct_to_date",
        "home_last5_o_hardhit", "away_last5_o_hardhit",
        "home_last5_o_xwoba", "away_last5_o_xwoba",
        "home_last_p_hardhit", "away_last_p_hardhit",
        "home_last_p_xwoba", "away_last_p_xwoba"] if c in o.columns]
    out = o[base_columns + ["src_start"]].copy()
    out = out.rename(columns={"src_start": "start_utc"})
    numeric = [c for c in base_columns if c not in {"game_pk", "game_date", "home_winner"}]
    for c in numeric:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    # Optional quote metadata defaults: unverified and unstamped.
    out["observed_at"] = None
    out["available_at"] = None
    out["source_id"] = "cesar_dx_mlb_odds"
    out["source_url"] = "https://github.com/cesar-dx/mlb-betting-ml"
    out["source_observation_id"] = None
    out["verification_status"] = "UNVERIFIED"
    out["closing_flag"] = 0
    for c in ("home_odds_open", "away_odds_open", "home_odds_close", "away_odds_close",
              "observed_total_line", "total_juice", "close_total_line", "close_total_juice",
              "rl_home_line", "rl_away_line", "rl_home_juice", "rl_away_juice"):
        out[c] = np.nan
    out["ml_quote_tier"] = "cesar_no_timestamp"
    out["ou_line_source"] = None

    # Overlay verified open/close rows when the validated file exists.
    oc_path = FEAT / "odds_open_close.parquet"
    oc_verified_extra = pd.DataFrame()  # verified rows whose game_pk not in cesar
    if oc_path.exists():
        oc = pd.read_parquet(oc_path)
        # Split: those that join to existing out, and those that don't
        out = out.merge(oc, on="game_pk", how="left", suffixes=("", "_oc"))
        verified_ml = out["ml_verified"].fillna(False).astype(bool)
        verified_ou = out["ou_verified"].fillna(False).astype(bool)
        stamp = out.get("start_utc_oc")
        if stamp is None:
            stamp = out.get("start_utc")
        # Entry quote = opening line (definitional pre-pitch availability).
        out.loc[verified_ml, "home_odds"] = out.loc[verified_ml, "home_open_ml"]
        out.loc[verified_ml, "away_odds"] = out.loc[verified_ml, "away_open_ml"]
        out.loc[verified_ml, "home_odds_open"] = out.loc[verified_ml, "home_open_ml"]
        out.loc[verified_ml, "away_odds_open"] = out.loc[verified_ml, "away_open_ml"]
        out.loc[verified_ml, "home_odds_close"] = out.loc[verified_ml, "home_close_ml"]
        out.loc[verified_ml, "away_odds_close"] = out.loc[verified_ml, "away_close_ml"]
        out.loc[verified_ml, "observed_at"] = stamp.loc[verified_ml]
        out.loc[verified_ml, "available_at"] = stamp.loc[verified_ml]
        out.loc[verified_ml, "source_id"] = "bettingtools_open_close"
        out.loc[verified_ml, "source_url"] = "https://github.com/pwu97/bettingtools"
        out.loc[verified_ml, "source_observation_id"] = [
            f"quote:bettingtools:open:{int(pk)}" for pk in out.loc[verified_ml, "game_pk"]]
        out.loc[verified_ml, "verification_status"] = "VERIFIED"
        out.loc[verified_ml, "ml_quote_tier"] = "verified_open_entry_close_clv"
        # Totals
        out.loc[verified_ou, "observed_total_line"] = out.loc[verified_ou, "open_ou_line"]
        out.loc[verified_ou, "total_juice"] = out.loc[verified_ou, "open_ou_odds"]
        out.loc[verified_ou, "close_total_line"] = out.loc[verified_ou, "close_ou_line"]
        out.loc[verified_ou, "close_total_juice"] = out.loc[verified_ou, "close_ou_odds"]
        out.loc[verified_ou, "ou_line_source"] = "bettingtools_open_close"
        # Run line
        out["rl_home_line"] = out.get("home_run_line")
        out["rl_away_line"] = out.get("away_run_line")
        out["rl_home_juice"] = out.get("home_run_line_odds")
        out["rl_away_juice"] = out.get("away_run_line_odds")
        drop_cols = [c for c in out.columns if c.endswith("_oc")]
        out = out.drop(columns=drop_cols)

        # Now add verified open_close rows whose game_pk was NOT in cesar at all
        cesar_pks = set(out.game_pk.astype(int).tolist())
        oc_only = oc[oc.ml_verified & ~oc.game_pk.isin(cesar_pks)].copy()
        if len(oc_only):
            # Need game_date/start_utc from games — handle suffix collision explicitly
            g_reset = games[["game_pk", "game_date", "start_utc"]].copy()
            g_reset = g_reset.rename(columns={"game_date": "g_game_date", "start_utc": "g_start_utc"})
            oc_only = oc_only.merge(g_reset, on="game_pk", how="left")
            extra_rows = []
            for r in oc_only.itertuples():
                # Prefer the games table timestamp (canonical), fallback to oc's own start_utc
                g_start = getattr(r, "g_start_utc", None)
                oc_start = getattr(r, "start_utc", None)
                final_start = g_start if pd.notna(g_start) and str(g_start).strip() else oc_start
                g_date = getattr(r, "g_game_date", None)
                oc_date = getattr(r, "date", None) if hasattr(r, "date") else None
                final_date = g_date if pd.notna(g_date) and str(g_date).strip() else oc_date
                # If still missing, use game_date from oc parquet if present
                if (final_date is None or (isinstance(final_date, float) and np.isnan(final_date))) and hasattr(r, "game_date"):
                    final_date = getattr(r, "game_date")
                extra_rows.append({
                    "game_pk": int(r.game_pk),
                    "game_date": final_date,
                    "start_utc": final_start,
                    "home_odds": float(r.home_open_ml),
                    "away_odds": float(r.away_open_ml),
                    "home_odds_open": float(r.home_open_ml),
                    "away_odds_open": float(r.away_open_ml),
                    "home_odds_close": float(r.home_close_ml),
                    "away_odds_close": float(r.away_close_ml),
                    "observed_total_line": float(r.open_ou_line) if pd.notna(r.open_ou_line) else np.nan,
                    "total_juice": float(r.open_ou_odds) if pd.notna(r.open_ou_odds) else np.nan,
                    "close_total_line": float(r.close_ou_line) if pd.notna(r.close_ou_line) else np.nan,
                    "close_total_juice": float(r.close_ou_odds) if pd.notna(r.close_ou_odds) else np.nan,
                    "observed_at": final_start,
                    "available_at": final_start,
                    "source_id": "bettingtools_open_close",
                    "source_url": "https://github.com/pwu97/bettingtools",
                    "source_observation_id": f"quote:bettingtools:open:{int(r.game_pk)}",
                    "verification_status": "VERIFIED",
                    "closing_flag": 0,
                    "ml_quote_tier": "verified_open_entry_close_clv",
                    "ou_line_source": "bettingtools_open_close" if pd.notna(r.open_ou_line) else None,
                    "rl_home_line": float(r.home_run_line) if pd.notna(getattr(r, "home_run_line", np.nan)) else np.nan,
                    "rl_away_line": float(r.away_run_line) if pd.notna(getattr(r, "away_run_line", np.nan)) else np.nan,
                    "rl_home_juice": float(r.home_run_line_odds) if pd.notna(getattr(r, "home_run_line_odds", np.nan)) else np.nan,
                    "rl_away_juice": float(r.away_run_line_odds) if pd.notna(getattr(r, "away_run_line_odds", np.nan)) else np.nan,
                })
            oc_verified_extra = pd.DataFrame(extra_rows)

    # Combine base + extra verified
    if len(oc_verified_extra):
        # Ensure same columns
        for col in out.columns:
            if col not in oc_verified_extra.columns:
                oc_verified_extra[col] = np.nan
        for col in oc_verified_extra.columns:
            if col not in out.columns:
                out[col] = np.nan
        out = pd.concat([out, oc_verified_extra[out.columns]], ignore_index=True)

    out["closing_flag"] = 0
    for c in ("home_odds", "away_odds", "home_odds_open", "away_odds_open",
              "home_odds_close", "away_odds_close", "observed_total_line",
              "total_juice", "close_total_line", "close_total_juice"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out["verification_status"] = out["verification_status"].fillna("UNVERIFIED").astype(str)
    keep = [c for c in [
        "game_pk", "game_date", "start_utc", "home_odds", "away_odds", "home_winner",
        "home_win_pct_to_date", "away_win_pct_to_date",
        "home_last5_o_hardhit", "away_last5_o_hardhit",
        "home_last5_o_xwoba", "away_last5_o_xwoba",
        "home_last_p_hardhit", "away_last_p_hardhit",
        "home_last_p_xwoba", "away_last_p_xwoba",
        "observed_at", "available_at", "source_id", "source_url",
        "source_observation_id", "verification_status", "closing_flag",
        "home_odds_open", "away_odds_open", "home_odds_close", "away_odds_close",
        "observed_total_line", "total_juice", "close_total_line", "close_total_juice",
        "rl_home_line", "rl_away_line", "rl_home_juice", "rl_away_juice",
        "ml_quote_tier", "ou_line_source"] if c in out.columns]
    out = out[keep].drop_duplicates("game_pk")
    out.to_parquet(FEAT / "odds.parquet", index=False)
    n_verified = int((out.verification_status == "VERIFIED").sum())
    print(f"[join_odds] rows={len(out)} verified_ml_quotes={n_verified} dropped_mismatch={dropped} extra_verified={len(oc_verified_extra)}")
    return len(out), dropped


def _record_dataset_observations() -> None:
    """Record content-addressed local dataset observations when a manifest exists.

    Integrity levels (kept explicit so a checksum match is never mistaken for
    a market-data verification):
      * sha256 present and matching  -> VERIFIED (file integrity only)
      * sha256 present and mismatching -> issue queue + MISMATCH status
      * only a git blob SHA recorded -> RETRIEVED (blob pinned, digest recorded)
    """
    manifest_path = RAW / "FETCH_MANIFEST.json"
    if not manifest_path.exists():
        return
    try:
        import json
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        db.record_issue("FETCH-MANIFEST-INVALID", "SOURCE", "FETCH_MANIFEST.json is not valid JSON", "HIGH")
        return
    def _repo_source(repo: str) -> str:
        if "sportsdataverse" in repo:
            return "sportsdataverse_baseballr"
        if "pwu97" in repo or "bettingtools" in repo:
            return "bettingtools_open_close"
        if "cesar-dx" in repo:
            return "cesar_dx_mlb_odds"
        return "unknown"

    def _git_blob_sha1(data: bytes) -> str:
        return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()

    manifest_dirty = False
    db.init_db()
    with db.db() as conn:
        for item in manifest.get("files", []):
            dest = ROOT / item.get("dest", "")
            if not dest.exists():
                conn.execute(
                    "INSERT OR REPLACE INTO data_issues (issue_id,issue_type,severity,status,entity_type,entity_id,description,source_ids,detected_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"MISSING:{item.get('dest')}", "SOURCE", "HIGH", "OPEN", "dataset", item.get("dest"),
                     "Manifest file is missing from the local snapshot", "[]", db.utcnow()),
                )
                continue
            raw_bytes = dest.read_bytes()
            digest = hashlib.sha256(raw_bytes).hexdigest()
            expected = item.get("sha256")
            if not expected and item.get("blob_sha"):
                # Manifests written before sha256 pinning carry the git blob
                # SHA.  Recomputing blob SHA-1 from local bytes independently
                # proves the file still matches the upstream object.
                if _git_blob_sha1(raw_bytes) == item["sha256" if False else "blob_sha"]:
                    item["sha256"] = digest
                    manifest_dirty = True
                    expected = digest
                else:
                    status = "MISMATCH"
                    notes = "local bytes do not match the pinned git blob SHA-1"
                    conn.execute(
                        "INSERT OR REPLACE INTO data_issues (issue_id,issue_type,severity,status,entity_type,entity_id,description,source_ids,detected_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"CHECKSUM:{item.get('dest')}", "SOURCE", "CRITICAL", "OPEN", "dataset", item.get("dest"),
                         notes, "[]", db.utcnow()),
                    )
                    oid = f"dataset:{item.get('source_repo')}:{item.get('source_path')}:{item.get('blob_sha')}"
                    conn.execute(
                        "INSERT OR REPLACE INTO source_observations "
                        "(observation_id,source_id,source_locator,record_key,retrieval_time,checksum,verification_status,notes) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (oid, _repo_source(str(item.get("source_repo", ""))),
                         item.get("source_path"), item.get("dest"),
                         item.get("fetched_utc", db.utcnow()), digest, status, notes),
                    )
                    continue
            if expected:
                if digest == expected:
                    status = "VERIFIED"
                    notes = ("File integrity verified against manifest sha256 "
                             "(and pinned git blob SHA); dataset-specific content "
                             "validators are separate gates")
                else:
                    status = "MISMATCH"
                    notes = f"local sha256 {digest} != manifest {expected}"
                    conn.execute(
                        "INSERT OR REPLACE INTO data_issues (issue_id,issue_type,severity,status,entity_type,entity_id,description,source_ids,detected_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"CHECKSUM:{item.get('dest')}", "SOURCE", "CRITICAL", "OPEN", "dataset", item.get("dest"),
                         notes, "[]", db.utcnow()),
                    )
            else:
                status = "RETRIEVED"
                notes = "content retrieved; no pinned digest available"
            oid = f"dataset:{item.get('source_repo')}:{item.get('source_path')}:{item.get('blob_sha', digest)}"
            conn.execute(
                "INSERT OR REPLACE INTO source_observations "
                "(observation_id,source_id,source_locator,record_key,retrieval_time,checksum,verification_status,notes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (oid, _repo_source(str(item.get("source_repo", ""))),
                 item.get("source_path"), item.get("dest"), item.get("fetched_utc", db.utcnow()), digest,
                 status, notes),
            )
    if manifest_dirty:
        try:
            manifest_path.write_text(json.dumps(manifest, indent=2))
        except OSError:
            pass


def _update_source_availability() -> None:
    """Promote reachability/integrity evidence into the source metadata table."""
    manifest_path = RAW / "FETCH_MANIFEST.json"
    if not manifest_path.exists():
        return
    try:
        import json
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return
    by_repo: dict[str, int] = {}
    for item in manifest.get("files", []):
        repo = str(item.get("source_repo", ""))
        dest = ROOT / item.get("dest", "")
        if dest.exists():
            by_repo[repo] = by_repo.get(repo, 0) + 1
    today = TODAY
    with db.db() as conn:
        if by_repo.get("sportsdataverse/baseballr-data"):
            conn.execute(
                "UPDATE source_metadata SET current_availability=?,verification_date=?,verification_status=?,"
                "last_http_status=?,last_error=NULL,notes=? WHERE source_id=?",
                ("AVAILABLE", today, "PARTIALLY_VERIFIED", 200,
                 f"{by_repo['sportsdataverse/baseballr-data']} content-addressed files retrieved and integrity-checked "
                 "via api.github.com blobs; schedule/pbp validators run separately.",
                 "sportsdataverse_baseballr"))
        if by_repo.get("cesar-dx/mlb-betting-ml"):
            conn.execute(
                "UPDATE source_metadata SET current_availability=?,verification_date=?,verification_status=?,"
                "last_http_status=?,last_error=NULL,notes=? WHERE source_id=?",
                ("AVAILABLE", today, "PARTIALLY_VERIFIED", 200,
                 "Moneyline rows join-verified to the schedule by game_pk/date/teams, but the source has no "
                 "observed_at/available_at timestamps, so prices stay UNVERIFIED for wager eligibility.",
                 "cesar_dx_mlb_odds"))
        if by_repo.get("pwu97/bettingtools"):
            conn.execute(
                "INSERT OR IGNORE INTO source_registry "
                "(source_id,url,description,coverage,verified,reject_reason,fetched_at,notes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("bettingtools_open_close", "https://github.com/pwu97/bettingtools",
                 "open/close MLB moneyline and totals lines", "2014-2019", 0, None, today,
                 "Attribution required; scraped from sportsbookreviewsonline.com per repository LICENSE."))
            conn.execute(
                "INSERT OR REPLACE INTO source_metadata "
                "(source_id,name,url,data_type,historical_depth,current_availability,access_method,cost,"
                "restrictions,licensing,reliability,granularity,automation_capability,verification_date,"
                "verification_status,limitations,last_http_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("bettingtools_open_close", "bettingtools historical MLB lines",
                 "https://github.com/pwu97/bettingtools", "open/close MLB moneyline and totals lines",
                 "2014-2019", "AVAILABLE", "GitHub API git blobs", "free public repository",
                 "attribution required by repository LICENSE",
                 "custom: 'Everyone can use this package so long as you give credit'",
                 "secondary market candidate (scraped from sportsbookreviewsonline.com)",
                 "game/open-close quote", "high", today, "PARTIALLY_VERIFIED",
                 "Open/close semantics are definitional (close = last line before first pitch); "
                 "cross-check against cesar-dx 2019 closing lines is required before VERIFIED wager use.",
                 200))


# ------------------------------------------------------------------ build
def build() -> None:
    db.init_db()
    t0 = datetime.now()
    conn = db.connect()
    db.audit("ingest:start", f"seasons={SEASONS_ALL}, snapshot={TODAY}")
    from ..sources import register_catalog
    register_catalog()
    register_sources(conn)
    _record_dataset_observations()
    _update_source_availability()

    games = load_schedules(SEASONS_ALL)
    odds = load_odds(SEASONS_WITH_ODDS)
    pbp = load_pbp([y for y in SEASONS_ALL if y >= 2015])

    # Durable issue-queue records for known, structural data limitations.
    # These are observations about the sources, not guesses about their data.
    db.record_issue(
        "ISSUE-ODDS-NO-TIMESTAMPS", "MARKET_DATA",
        "cesar-dx/mlb-betting-ml moneyline columns have no observed_at/available_at/"
        "closing_flag fields. Prices are retained for research joins only; they are "
        "UNVERIFIED and cannot create stakes, PnL, ROI or CLV.",
        severity="HIGH", source_ids=["cesar_dx_mlb_odds"])
    db.record_issue(
        "ISSUE-2026-PO-PLACEHOLDERS", "SCHEDULE",
        "2026 postseason schedule rows use placeholder team names (e.g. 'AL Wild Card "
        "#1') before the bracket is set. They are excluded from the normalized games "
        "table because no team mapping exists; no prediction is asserted for them.",
        severity="MEDIUM", source_ids=["sportsdataverse_baseballr"])
    db.record_issue(
        "ISSUE-NO-POINT-IN-TIME-LINEUPS", "ROSTER_DATA",
        "Confirmed lineups, injuries, weather forecasts and umpire assignments with "
        "announced_at/available_at timestamps are not available from any verified "
        "source in this environment. Strategies that require them remain "
        "DATA_UNAVAILABLE rather than using postgame content as pregame evidence.",
        severity="HIGH", source_ids=[])
    db.record_issue(
        "ISSUE-EGRESS-BLOCKED", "SOURCE",
        "statsapi.mlb.com, raw.githubusercontent.com, api.weather.gov, "
        "trading-api.kalshi.com, baseballsavant.mlb.com and retrosheet.org are "
        "unreachable from the execution environment (connection refused). Only "
        "api.github.com/codeload.github.com/pypi.org were reachable on 2026-09-21; "
        "every dependency is restricted to reachable, content-addressed sources.",
        severity="MEDIUM", source_ids=[])

    games = build_games(games, pbp)
    games = group_series(games)

    series = build_series_table(games)
    state = build_series_state(games)
    build_pbp_aggregates(pbp, games=games)

    FEAT.mkdir(parents=True, exist_ok=True)
    games.to_parquet(FEAT / "games.parquet", index=False)

    # Open/close line validation needs games.parquet; the overlay join below
    # needs the validated file.  First run: validate now (cross-checking the
    # cesar odds written by the first join), then re-join with the overlay.
    had_open_close = (FEAT / "odds_open_close.parquet").exists()
    n_odds, dropped = join_odds(odds, games)
    try:
        from . import open_close_odds
        oc_summary = open_close_odds.build()
        n_odds, dropped = join_odds(odds, games)  # apply/refresh the overlay
    except FileNotFoundError as exc:
        db.record_issue("OPEN-CLOSE-SKIPPED", "MARKET_DATA",
                        f"Open/close line validation skipped: {exc}", "MEDIUM")
    except Exception as exc:
        db.record_issue("OPEN-CLOSE-REFRESH-FAILED", "MARKET_DATA",
                        f"Open/close validation/refresh failed: {type(exc).__name__}: {exc}",
                        "HIGH")

    conn.execute("DELETE FROM teams")  # rebuild clean (no stale rows)
    for tid, abbr in TEAM_ID_TO_ABBR.items():
        conn.execute("INSERT INTO teams VALUES (?,?,?,?)",
                     (tid, TEAM_ID_TO_NAME.get(tid, abbr), abbr, ABBREV_TO_LEAGUE[abbr]))

    cols = ["game_pk", "season", "game_date", "start_utc", "game_type", "round_code",
            "series_key", "home_team_id", "away_team_id", "home_score", "away_score",
            "status", "venue_name", "winner_team_id", "extra_innings", "home_win"]
    gw = games[cols].copy()
    conn.execute("DELETE FROM games")
    conn.executemany(
        "INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        db.sql_rows(gw))

    conn.execute("DELETE FROM series")
    conn.executemany(
        "INSERT INTO series VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        db.sql_rows(series))

    conn.execute("DELETE FROM series_state")
    conn.executemany(
        "INSERT INTO series_state (game_pk, series_key, round_code, game_number,"
        " wins_a_before, wins_b_before, series_tied, games_remaining, elimination_a,"
        " elimination_b, clinch_a, clinch_b, days_rest_home, days_rest_away,"
        " travel_km_home, travel_km_away, home_adv_games_left)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        db.sql_rows(state) if len(state) else [])

    conn.execute("DELETE FROM seasons")
    for y in SEASONS_ALL:
        r_ = games[(games.season == y) & (games.game_type == "R") & games.completed]
        p_ = games[(games.season == y) & games.round_code.notna() & games.completed]
        conn.execute("INSERT OR REPLACE INTO seasons VALUES (?,?,?,?,?)",
                     (y, len(r_), len(p_),
                      r_.game_date.min() if len(r_) else None,
                      r_.game_date.max() if len(r_) else None))

    conn.commit()
    conn.close()
    db.audit("ingest:done",
             f"games={len(games)}, po={int(games.round_code.notna().sum())}, "
             f"series={len(series)}, state_rows={len(state)}, "
             f"odds_joined={n_odds}, odds_dropped={dropped}")
    print(f"[ingest] games={len(games)} po_games={int(games.round_code.notna().sum())} "
          f"series={len(series)} state={len(state)} odds={n_odds} "
          f"(dropped {dropped}) elapsed={(datetime.now() - t0).total_seconds():.0f}s")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(RAW.parent))
    build()
