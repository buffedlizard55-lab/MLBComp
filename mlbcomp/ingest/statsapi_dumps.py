"""Parse official Stats API game dumps mirrored on GitHub into normalized rows.

Source: sportsdataverse/baseballr-data ``mlb/raw/{season}/{game_pk}.json.gz`` —
gzip JSON captured from the official MLB Stats API live feed for individual
games.  The sandbox cannot reach statsapi.mlb.com directly; this mirror is
content-addressed through the GitHub blobs API.

Availability semantics (documented, not invented):

* **probable pitchers** — published by MLB before first pitch; content in a
  postgame dump equals the pregame probable pair.  ``announced_at`` stays
  NULL (the dump does not record when MLB first posted them);
  verification is PARTIALLY_VERIFIED with an explicit note.
* **weather** — the feed's weather block describes conditions at game time;
  stamped at scheduled first pitch, which is exactly the decision timestamp.
* **umpires** — the calibrated crew is set before first pitch; ``assigned_at``
  stays NULL with the same documented assumption.

No row is created for a field the dump does not contain.  Missing dumps stay
missing — they are never backfilled from another source's guess.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .. import db
from ..config import RAW

DUMP_DIR = RAW / "statsapi"


def _parse_dump(path: Path) -> dict | None:
    try:
        with gzip.open(path, "rb") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    gd = data.get("gameData") or {}
    live = data.get("liveData") or {}
    game = gd.get("game") or {}
    out: dict = {"game_pk": int(game.get("pk")) if str(game.get("pk", "")).isdigit() else None}
    if out["game_pk"] is None:
        return None
    probables = gd.get("probablePitchers") or {}
    for side in ("home", "away"):
        entry = probables.get(side) or {}
        pid = entry.get("id")
        out[f"probable_{side}"] = int(pid) if pid is not None else None
        out[f"probable_{side}_name"] = entry.get("fullName")
    weather = gd.get("weather") or {}
    out["weather_temp_f"] = _temp(weather.get("temperature"))
    out["weather_condition"] = weather.get("condition")
    wind = weather.get("wind")
    out["weather_wind"] = wind if isinstance(wind, str) else None
    officials = (((live.get("boxscore") or {}).get("officials")) or [])
    out["umpires"] = [
        {"official_id": o.get("official", {}).get("id"),
         "full_name": o.get("official", {}).get("fullName"),
         "position": (o.get("officialPosition") or {}).get("name")}
        for o in officials if o.get("official")
    ] or None
    info = gd.get("gameInfo") or {}
    out["first_pitch"] = info.get("firstPitch")
    return out


def _temp(value) -> float | None:
    if value is None:
        return None
    text = str(value).replace("F", "").replace("°", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def build() -> dict:
    db.init_db()
    paths = sorted(DUMP_DIR.glob("*/*.json.gz"))
    parsed = [p for p in (map(_parse_dump, paths))]
    rows = [r for r in parsed if r]
    games = pd.DataFrame(rows).drop_duplicates("game_pk")
    if games.empty:
        return {"status": "DATA_UNAVAILABLE", "dumps": len(paths), "parsed": 0}

    # Restrict to games we actually track.
    tracked = db.query_df("SELECT game_pk, start_utc FROM games")
    games = games.merge(tracked, on="game_pk", how="inner")

    now = db.utcnow()
    n_probable = n_weather = n_umpire = 0
    with db.db() as conn:
        conn.execute("DELETE FROM starting_pitchers WHERE record_id LIKE 'sp:statsapi:%'")
        conn.execute("DELETE FROM weather_observations WHERE weather_id LIKE 'wx:statsapi:%'")
        conn.execute("DELETE FROM umpires WHERE assignment_id LIKE 'ump:statsapi:%'")
        for r in games.itertuples():
            pk = int(r.game_pk)
            start = getattr(r, "start_utc", None)
            for side in ("home", "away"):
                pid = getattr(r, f"probable_{side}")
                if pid is None:
                    continue
                name = getattr(r, f"probable_{side}_name", None)
                if pid is not None and name:
                    conn.execute(
                        "INSERT OR IGNORE INTO players (player_id, name) VALUES (?,?)",
                        (int(pid), str(name)))
                conn.execute(
                    "INSERT OR REPLACE INTO starting_pitchers "
                    "(record_id,game_pk,team_id,player_id,announced_at,available_at,"
                    "source_observation_id,verification_status) VALUES (?,?,?,?,?,?,?,?)",
                    (f"sp:statsapi:{pk}:{side}", pk, None, int(pid), None, str(start) if start else None,
                     f"dataset:sportsdataverse_baseballr:mlb/raw/{pk}.json.gz",
                     "PARTIALLY_VERIFIED"))
                n_probable += 1
            if getattr(r, "weather_temp_f", None) is not None or getattr(r, "weather_condition", None):
                conn.execute(
                    "INSERT OR REPLACE INTO weather_observations "
                    "(weather_id,game_pk,observed_at,temperature,wind_speed,precipitation,"
                    "roof_status,source_observation_id,verification_status) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"wx:statsapi:{pk}", pk, str(start) if start else None,
                     getattr(r, "weather_temp_f", None), None, None,
                     getattr(r, "weather_condition", None),
                     f"dataset:sportsdataverse_baseballr:mlb/raw/{pk}.json.gz",
                     "PARTIALLY_VERIFIED"))
                n_weather += 1
            for i, u in enumerate(getattr(r, "umpires", None) or []):
                conn.execute(
                    "INSERT OR REPLACE INTO umpires "
                    "(assignment_id,game_pk,umpire_id,crew_role,assigned_at,"
                    "source_observation_id,verification_status) VALUES (?,?,?,?,?,?,?)",
                    (f"ump:statsapi:{pk}:{i}", pk, u.get("official_id"),
                     u.get("position") or u.get("full_name"), None,
                     f"dataset:sportsdataverse_baseballr:mlb/raw/{pk}.json.gz",
                     "PARTIALLY_VERIFIED"))
                n_umpire += 1

    db.record_issue(
        "ISSUE-STATSAPI-MIRROR-ONLY", "SOURCE",
        "Probable pitchers, weather and umpires come from a GitHub mirror of the "
        "official Stats API feed (statsapi.mlb.com is unreachable from this "
        "environment). Announcement/assignment timestamps are not present in the "
        "dumps; rows are PARTIALLY_VERIFIED with availability assumed no later "
        "than first pitch per official MLB publishing rules.",
        severity="MEDIUM", source_ids=["sportsdataverse_baseballr"])
    summary = {"status": "COMPLETED", "dumps": len(paths), "parsed_games": int(len(games)),
               "probable_pitcher_rows": n_probable, "weather_rows": n_weather,
               "umpire_rows": n_umpire,
               "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    db.audit("statsapi_dumps:ingested", json.dumps(summary))
    print(f"[statsapi_dumps] parsed={summary['parsed_games']} probables={n_probable} "
          f"weather={n_weather} umpires={n_umpire}")
    return summary


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
