"""Fetch candidate postseason raw files for a source-validation run.

Fetches ``mlb/raw/<season>/<game_pk>.json.gz`` from the configured GitHub
mirror for postseason game IDs already present in a local schedule snapshot.
A 404 means that the requested path was not retrieved; it is an availability
issue to record, not evidence that a game ID or result was fabricated.  This
script does not create fallback rows, scores, winners, odds, or settlement
labels.  The resulting files still require manifest, license and content
validation before research use.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
import concurrent.futures as cf

import pandas as pd

GITHUB = os.environ.get("GITHUB_TOKEN", "")
REPO = "sportsdataverse/baseballr-data"
RAW_DIR = "data/raw/statsapi"
os.makedirs(RAW_DIR, exist_ok=True)


def fetch_one(year: int, pk: int) -> tuple[int, int, str]:
    dest = f"{RAW_DIR}/{year}/{pk}.json.gz"
    os.makedirs(f"{RAW_DIR}/{year}", exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return (year, pk, "cached")
    url = f"https://api.github.com/repos/{REPO}/contents/mlb/raw/{year}/{pk}.json.gz"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB}",
        "Accept": "application/vnd.github.raw",
        "User-Agent": "mlbcomp",
    })
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                blob = r.read()
            if len(blob) < 1000:
                return (year, pk, "too-small")
            with open(dest, "wb") as f:
                f.write(blob)
            return (year, pk, "ok")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return (year, pk, "404")
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return (year, pk, "error")


def main():
    games = pd.read_parquet("data/features/games.parquet")
    po = games[games.round_code.notna() & (games.round_code != "nan")]
    jobs = [(int(g.season), int(g.game_pk)) for g in po.itertuples()]
    print(f"postseason games to fetch: {len(jobs)}", flush=True)
    stats = {}
    done = 0
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for y, pk, st in ex.map(fetch_one, [j[0] for j in jobs], [j[1] for j in jobs]):
            stats[st] = stats.get(st, 0) + 1
            done += 1
            if done % 40 == 0:
                print(f"  {done}/{len(jobs)}  stats={stats}", flush=True)
    print("DONE", stats, flush=True)


if __name__ == "__main__":
    main()
