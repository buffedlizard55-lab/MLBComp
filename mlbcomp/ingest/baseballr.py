"""Source registry + canonical data build (ingest).

Sources
-------
S1  sportsdataverse/baseballr-data  (github.com)
    MLB schedule (1988-2026) + play-by-play (1988-2026) parquet exports.
    2026 file is live through 2026-09-20 (snapshot date).
S2  cesar-dx/mlb-betting-ml (github.com)
    Real moneyline odds (American) + outcomes + per-game statcast form
    features, 2019-2025 REGULAR SEASON ONLY (verified: files end at the
    2025 regular season; the 2021 file ends 2021-09-27).  No verified
    postseason prices exist in this environment.

POSTSEASON AUTHENTICITY (re-audited 2026-09-21 — supersedes the 2026-09-20
claim that the mirror's postseason was "partially fabricated"):
    The S1 mirror's postseason is AUTHENTIC.  Its World Series rows for all
    eleven seasons 2015-2025 reproduce the independently documented champions
    and series lengths exactly (2015 KC 4-1 NYM, 2016 CHC 4-3 CLE, 2017 HOU
    4-3 LAD, 2018 BOS 4-1 LAD, 2019 WAS 4-3 HOU, 2020 LAD 4-2 TB, 2021 ATL
    4-2 HOU, 2022 HOU 4-2 PHI, 2023 TEX 4-1 ARI, 2024 LAD 4-1 NYY, 2025 LAD
    4-3 TOR).  The earlier "fabrication" finding was an artefact of a wrong
    KNOWN_WS_CHAMPIONS table in this file (5 of 11 champions were wrong),
    not a property of the source.  KNOWN_WS_CHAMPIONS is now correct and
    verify/checks.py fails loudly on any mismatch instead of quarantining.
    The mirror's real postseason scores (502 games, 2015-2026) are therefore
    used directly; see features/po_corpus.py.

    Known source quirks that ARE real and are handled, not quarantined:
      * 2022 WS Game 3 (2022-10-31) was rain-suspended and completed the next
        day; the mirror carries a Final row with null scores.  Rows with a
        null score are treated as unsettled (never scored), and the series is
        still reconstructed correctly from the remaining games.

Rejected / unavailable (flagged per spec §35 — no invented data):
R1  statsapi.mlb.com (MLB Stats API) — network-blocked in this environment.
R2  baseballsavant.com (Statcast) — network-blocked.
R3  fivethirtyeight/data/mlb (historical odds 2015-2020) — dataset removed
    from the repo in 2024; forks re-synced, no longer available.
R4  OddsPortal / sportsbook historical odds scrapers — no verified source.
R5  Weather feeds (OpenWeather, NWS) — network-blocked; weather strategies
    are marked DATA_UNAVAILABLE instead of estimated.
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from .. import db
from ..config import FEAT, RAW, SEASONS_ALL, SEASONS_WITH_ODDS, TODAY

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
# NOTE: the source's numeric team ids are NOT stable across years (e.g. the
# Padres are 135 in 2025 but 124 in 2019).  game_pk and team NAME/abbreviation
# ARE stable.  We therefore use the abbreviation as the canonical key and
# assign our own stable canonical team ids (independent of the source ids).
CANON_ABBR_TO_ID = {abbr: i + 1 for i, abbr in enumerate(sorted(ABBREV_TO_LEAGUE.keys()))}
TEAM_ID_TO_ABBR = {tid: abbr for abbr, tid in CANON_ABBR_TO_ID.items()}
TEAM_ID_TO_NAME: dict[int, str] = {}  # filled with latest display name per team

# Independently documented World Series champions 2015-2025 (public record).
# Cross-checked 2026-09-21 against four independent public listings
# (mlbschedule.net, baseballnewsplus.com, surprisesports.com,
# baseballstandard.com) which agree on every season listed here.  These are
# used ONLY as an external cross-check of the ingested mirror; the mirror is
# the data source.  A mismatch is a hard verification FAILURE.
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
    rows = [
        ("S1_baseballr_data", "https://github.com/sportsdataverse/baseballr-data",
         "MLB schedule + play-by-play parquet, 1988-2026 (2026 live through 2026-09-20)",
         "games, scores, venues, series, at-bats, pitchers, batters", 1, None,
         db.utcnow(),
         "fetched per-season via api.github.com git-blobs into data/raw/baseballr "
         "(blob SHAs recorded in data/raw/FETCH_MANIFEST.json). REGULAR SEASON "
         "2015-2026 trusted (2019-2025 independently cross-verified via S2: "
         "date+team join with zero mismatches and 100% outcome agreement). "
         "POSTSEASON 2015-2025 also trusted: its World Series rows reproduce all "
         "11 independently documented champions and series lengths exactly "
         "(see KNOWN_WS_CHAMPIONS). The 2026-09-20 claim that this source's "
         "postseason was fabricated was wrong — it was caused by an incorrect "
         "champion table, not by the data."),
        ("S2_cesar_odds", "https://github.com/cesar-dx/mlb-betting-ml",
         "Real moneyline odds (American) + outcomes + statcast form features",
         "2019-2025 REGULAR SEASON ONLY (verified: 2021 file ends 2021-09-27; "
         "no postseason odds in any year)",
         1, None, db.utcnow(),
         "cloned into data/raw/odds. 15,442 rows joined with 0 mismatches. "
         "NO verified market prices exist for any 2019-2025 postseason game -> "
         "PO ROI is reported as a labeled fair-coin proxy, not real market ROI."),
        ("S3_statsapi_mirror", "https://github.com/sportsdataverse/baseballr-data (mlb/raw)",
         "Per-game MLB Stats API responses (json.gz), 1988-2026",
         "440 postseason dumps fetched by game_pk into data/raw/statsapi", 0,
         "NOT an independent source: same mirror family as S1; all 440 dumps "
         "carry null scores, so they corroborate dates/teams only", db.utcnow(),
         "dates/teams match S1 schedule for all 440 pks. NOTE: the earlier claim "
         "that a '2016 WS' dump showed a game that never occurred was wrong — "
         "2016-10-25 Cubs@Indians was World Series Game 1 and did occur. "
         "Still not used for settlement (null scores)."),
        ("R1_statsapi", "https://statsapi.mlb.com",
         "MLB Stats API (live boxscores, weather, lineups)", "—", 0,
         "network-blocked in this environment", db.utcnow(),
         "live 2026 data comes from the S1 snapshot instead"),
        ("R2_baseballsavant", "https://www.baseballsavant.com",
         "Statcast pitch-level data", "—", 0,
         "network-blocked in this environment", db.utcnow(),
         "K/BB/HR rates approximated from S1 play-by-play events"),
        ("R3_fivethirtyeight", "https://github.com/fivethirtyeight/data",
         "Historical odds 2015-2020", "—", 0,
         "dataset removed from repo (2024); forks re-synced empty", db.utcnow(),
         "market strategies therefore backtest 2019-2025 only"),
        ("R4_oddsportal", "https://www.oddsportal.com",
         "Historical sportsbook odds", "—", 0,
         "no verifiable scrape available without API key; anti-bot", db.utcnow(),
         "rejected rather than estimated"),
        ("R5_weather", "OpenWeather/NWS", "Game-day weather", "—", 0,
         "network-blocked; S1 schedule has no weather field", db.utcnow(),
         "weather-dependent hypotheses flagged DATA_UNAVAILABLE"),
    ]
    for r in rows:
        conn.execute("INSERT OR REPLACE INTO source_registry VALUES (?,?,?,?,?,?,?,?)", r)


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
                p, columns=["game_pk", "at_bat_index", "inning", "batter_id",
                            "pitcher_id", "event_type", "event"]))
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

    WC: single-elimination game in 2012/2015 (1 win); best-of-3 in 2020 and
    from 2022 on (2 wins).
    DS: best-of-5 (3 wins) in EVERY season here, including 2020.  The 2020
    expanded format lengthened the WILD CARD round to best-of-three but left
    the Division Series at its normal best-of-five (MLB's own 2020-07-23
    announcement: "Division Series (best-of-five ... at neutral sites)").
    LCS/WS: best-of-7 (4 wins).
    """
    if round_code == "WC":
        return 1 if season in (2012, 2015) else 2
    if round_code == "DS":
        return 3
    return 4  # LCS / WS


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


def build_pbp_aggregates(pbp: pd.DataFrame) -> None:
    """Play-by-play -> pitcher_game + game_events parquet (2015+)."""
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


def join_odds(odds: pd.DataFrame, games: pd.DataFrame) -> int:
    """Join verified market odds onto games; verify teams+date match (S1 vs S2)."""
    g = games.set_index("game_pk")
    o = odds.merge(
        g[["game_date", "home_abbr", "away_abbr"]].rename(
            columns={"game_date": "src_date", "home_abbr": "src_home",
                     "away_abbr": "src_away"}).reset_index(),
        on="game_pk", how="inner")
    mismatch = ((o.game_date != o.src_date) | (o.home_name != o.src_home) |
                (o.away_name != o.src_away))
    dropped = int(mismatch.sum())
    o = o[~mismatch]
    out = o[["game_pk", "game_date", "home_odds", "away_odds", "home_winner",
             "home_win_pct_to_date", "away_win_pct_to_date",
             "home_last5_o_hardhit", "away_last5_o_hardhit",
             "home_last5_o_xwoba", "away_last5_o_xwoba",
             "home_last_p_hardhit", "away_last_p_hardhit",
             "home_last_p_xwoba", "away_last_p_xwoba"]].copy()
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out.to_parquet(FEAT / "odds.parquet", index=False)
    return len(out), dropped


# ------------------------------------------------------------------ build
def build() -> None:
    db.init_db()
    t0 = datetime.now()
    conn = db.connect()
    db.audit("ingest:start", f"seasons={SEASONS_ALL}, snapshot={TODAY}")
    register_sources(conn)

    games = load_schedules(SEASONS_ALL)
    odds = load_odds(SEASONS_WITH_ODDS)
    pbp = load_pbp([y for y in SEASONS_ALL if y >= 2015])

    games = build_games(games, pbp)
    games = group_series(games)

    series = build_series_table(games)
    state = build_series_state(games)
    build_pbp_aggregates(pbp)
    n_odds, dropped = join_odds(odds, games)

    games.to_parquet(FEAT / "games.parquet", index=False)

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
