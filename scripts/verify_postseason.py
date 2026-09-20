"""Cross-verify every postseason game: raw Stats API dump vs schedule parquet.

Writes data/features/po_verified.parquet: the verified postseason corpus
(source of truth = raw API dump; schedule rows that contradict it are
flagged).  Also prints a per-season discrepancy report.
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import glob
import gzip
import json
import sqlite3

import pandas as pd

from mlbcomp.config import FEAT

ABB_FIX = {"WSH": "WAS", "OAK": "ATH"}


def canon_abbr(a: str) -> str:
    return ABB_FIX.get(a, a)


def parse_dump(path: str) -> dict:
    with gzip.open(path, "rb") as f:
        d = json.load(f)
    gd = d["gameData"]
    ld = d.get("liveData", {})
    g = gd["game"]
    teams = gd["teams"]
    away, home = teams["away"], teams["home"]
    row = {
        "game_pk": int(g["pk"]),
        "date_raw": gd["datetime"]["officialDate"],
        "season_raw": int(str(g.get("season", "")) or 0),
        "game_type_raw": g["type"],
        "home_abbr_raw": canon_abbr(home.get("abbreviation", "?")),
        "away_abbr_raw": canon_abbr(away.get("abbreviation", "?")),
        "home_name_raw": home.get("name"),
        "away_name_raw": away.get("name"),
        "home_score_raw": pd.to_numeric(home.get("score"), errors="coerce"),
        "away_score_raw": pd.to_numeric(away.get("score"), errors="coerce"),
        "status_raw": gd["status"]["detailedState"],
        "venue_raw": gd.get("venue", {}).get("name"),
        "weather_raw": str((gd.get("weather") or {}).get("summary", "") or
                            (gd.get("weather") or {}).get("temperature", "")),
        "series_raw": str(gd.get("gameInfo", {}).get("series", "") or "")[:80],
        "game_number_raw": g.get("gameNumber"),
    }
    if pd.isna(row["home_score_raw"]):
        ls = ld.get("linescore", {}).get("runs")
        if isinstance(ls, dict):
            row["home_score_raw"] = pd.to_numeric(ls.get("home"), errors="coerce")
            row["away_score_raw"] = pd.to_numeric(ls.get("away"), errors="coerce")
    pp = gd.get("probablePitchers", {}) or {}
    ph = pp.get("home", [])
    pa = pp.get("away", [])
    row["pp_home"] = ph[0].get("name") if isinstance(ph, list) and ph else None
    row["pp_away"] = pa[0].get("name") if isinstance(pa, list) and pa else None
    return row


def main():
    rows = []
    for path in sorted(glob.glob("data/raw/statsapi/*/*.json.gz")):
        try:
            rows.append(parse_dump(path))
        except Exception as e:
            print(f"PARSE FAIL {path}: {e}")
    raw = pd.DataFrame(rows).drop_duplicates("game_pk").set_index("game_pk")
    print(f"raw dumps parsed: {len(raw)}")

    g = pd.read_parquet(FEAT / "games.parquet")
    po = g[g.round_code.notna()].copy()
    conn = sqlite3.connect(FEAT.parent / "mlbcomp.db")
    teams = pd.read_sql("SELECT * FROM teams", conn)
    id2ab = dict(zip(teams.team_id, teams.abbr))
    po["home_abbr_sch"] = po.home_team_id.map(id2ab)
    po["away_abbr_sch"] = po.away_team_id.map(id2ab)

    m = po.merge(raw, left_on="game_pk", right_index=True, how="inner")
    print(f"matched schedule<->raw: {len(m)} (of {len(po)} schedule po games)")
    missing = set(po.game_pk) - set(raw.index)
    if missing:
        print("MISSING raw dumps for schedule pks:", sorted(missing))

    m["home_score_sch"] = pd.to_numeric(m.home_score, errors="coerce")
    m["away_score_sch"] = pd.to_numeric(m.away_score, errors="coerce")
    m["date_match"] = m.game_date.astype(str).str[:10] == m.date_raw.astype(str).str[:10]
    m["teams_match"] = (m.home_abbr_sch == m.home_abbr_raw) & (m.away_abbr_sch == m.away_abbr_raw)
    m["score_match"] = (m.home_score_sch == m.home_score_raw) & (m.away_score_sch == m.away_score_raw)
    m["all_match"] = m.date_match & m.teams_match & m.score_match

    bad = m[~m.all_match]
    print(f"\nDISCREPANCIES: {len(bad)} of {len(m)}")
    cols = ["season", "game_date", "home_abbr_sch", "away_abbr_sch", "home_score_sch",
            "away_score_sch", "date_raw", "home_abbr_raw", "away_abbr_raw",
            "home_score_raw", "away_score_raw", "date_match", "teams_match", "score_match"]
    print(bad[cols].to_string(index=False))

    ok = m[m.all_match]
    out = ok[["game_pk", "season", "game_date", "round_code", "home_abbr_sch",
              "away_abbr_sch", "home_score_sch", "away_score_sch", "venue_raw",
              "weather_raw", "pp_home", "pp_away", "game_number_raw", "series_raw"]].copy()
    out = out.rename(columns={"home_abbr_sch": "home_abbr", "away_abbr_sch": "away_abbr",
                              "home_score_sch": "home_score", "away_score_sch": "away_score"})
    out.to_parquet(FEAT / "po_verified.parquet", index=False)
    print(f"\nverified OK: {len(out)} -> {FEAT / 'po_verified.parquet'}")
    print("\nper-season verified counts:")
    print(out.groupby("season").size().to_string())


if __name__ == "__main__":
    main()
