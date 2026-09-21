"""Verified postseason corpus — built from the authentic mirror.

RE-AUDIT 2026-09-21 (supersedes the previous version of this module):

    The previous version built a 256-game corpus in which 2019-2024 came from
    `data_recon/po_results.py`, a hand-written reconstruction of "game winners"
    created because the mirror's postseason was believed to be fabricated.
    Both halves of that were wrong:

      1. The mirror is authentic.  Its World Series rows reproduce all eleven
         independently documented champions 2015-2025 (see
         ingest.baseballr.KNOWN_WS_CHAMPIONS).
      2. The reconstruction itself contained invented results, e.g. it asserted
         the Rays beat the Dodgers in the 2020 WS (the Dodgers won 4-2), the
         Astros beat the Braves in 2021 (the Braves won 4-2), the Dodgers
         swept the Rangers in 2023 (the Rangers beat Arizona 4-1; Los Angeles
         was not in that Series), the Yankees beat the Dodgers in 2024 (the
         Dodgers won 4-1), the Cardinals beat the Nationals in the 2019 NLCS
         (Washington swept St. Louis 4-0) and the Nationals winning the 2019
         WS 4-1 (they won 4-3).

    Using invented winners as settlement labels is exactly the failure mode
    this project exists to avoid, so the reconstruction is retired.  This
    module now takes the postseason straight from the mirror — real game_pk,
    real dates, real home/away, real scores — and *fails loudly* if the
    resulting World Series champions do not match the independent list.

Provenance tagging is retained (spec: preserve provenance):
    provenance = "mirror"          every row (single source, cross-checked)
    score_available = False        for rows the source marks Final but with
                                   null scores (e.g. the rain-suspended
                                   2022 WS Game 3) — never settled on score.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from ..config import FEAT
from ..ingest.baseballr import KNOWN_WS_CHAMPIONS


def build_po_corpus() -> pd.DataFrame:
    conn = sqlite3.connect(FEAT.parent / "mlbcomp.db")
    teams = pd.read_sql("SELECT * FROM teams", conn)
    conn.close()
    id2abbr = {int(r.team_id): r.abbr for r in teams.itertuples()}

    g = pd.read_parquet(FEAT / "games.parquet")
    po = g[g.round_code.notna()].copy()

    cols = ["game_pk", "season", "game_date", "round_code", "league",
            "home_team_id", "away_team_id", "home_score", "away_score",
            "home_abbr", "away_abbr", "winner_team_id", "series_key",
            "completed"]
    missing = [c for c in cols if c not in po.columns]
    if missing:
        raise RuntimeError(f"games.parquet missing columns: {missing}")
    out = po[cols].copy()
    out["provenance"] = "mirror"
    out["confidence"] = "high"
    out["source"] = ("sportsdataverse/baseballr-data schedule "
                     "(cross-checked vs KNOWN_WS_CHAMPIONS)")

    # A game is usable for score-based research only when both scores exist.
    both = out.home_score.notna() & out.away_score.notna()
    out["score_available"] = both & out.completed.astype(bool)

    # ---- hard cross-check: the corpus must reproduce documented champions --
    id2abbr_local = id2abbr
    bad = []
    for y, known in KNOWN_WS_CHAMPIONS.items():
        ws = out[(out.season == y) & (out.round_code == "WS") &
                 out.winner_team_id.notna()]
        if not len(ws):
            bad.append(f"{y}: no settled WS games in corpus (known={known})")
            continue
        last = ws.sort_values(["game_date", "game_pk"]).iloc[-1]
        got = id2abbr_local.get(int(last.winner_team_id), "?")
        if got != known:
            bad.append(f"{y}: corpus={got} known={known}")
    if bad:
        raise RuntimeError(
            "postseason corpus failed the independent champion cross-check: "
            + "; ".join(bad))

    out.to_parquet(FEAT / "po_corpus.parquet", index=False)
    settled = int(out.winner_team_id.notna().sum())
    print(f"po_corpus: {len(out)} postseason games from the mirror "
          f"({settled} settled, {int(out.score_available.sum())} with scores) "
          f"-> {FEAT / 'po_corpus.parquet'}")
    print("champion cross-check PASSED for "
          f"{min(KNOWN_WS_CHAMPIONS)}-{max(KNOWN_WS_CHAMPIONS)}")
    print(out[out.completed.astype(bool)]
          .groupby(["season", "round_code"]).size().unstack(fill_value=0).to_string())
    return out


if __name__ == "__main__":
    build_po_corpus()
