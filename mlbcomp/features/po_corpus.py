"""Optional source-gated postseason projection.

The clean repository contains no postseason snapshot.  This adapter can
project a normalized, source-backed ``games.parquet`` into a postseason corpus
only after an operator has fetched and validated that source.  It never fills
missing scores, reconstructs winners from series summaries, invents game IDs,
or treats a reference champion list as a settlement source.

Every output row carries a provenance label and a score-availability flag.
Rows with missing scores remain available for schedule/state research only and
are excluded from score-based settlement and performance calculations.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from ..config import FEAT


def build_po_corpus() -> pd.DataFrame:
    conn = sqlite3.connect(FEAT.parent / "mlbcomp.db")
    observations = pd.read_sql(
        "SELECT COUNT(*) AS n FROM source_observations "
        "WHERE verification_status='VERIFIED' AND "
        "(record_key LIKE '%schedule%' OR source_locator LIKE '%schedule%' "
        "OR record_key LIKE '%game%' OR source_locator LIKE '%game%')", conn)
    if int(observations.iloc[0].n) == 0:
        conn.close()
        raise RuntimeError("no VERIFIED schedule/game source observation; refusing to project postseason rows")
    conn.close()

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
    out["provenance"] = "VERIFIED source schedule observation"
    out["confidence"] = "source-validated; score validation remains row-level"
    out["source"] = "source_observations; schedule/game manifest required"

    # A game is usable for score-based research only when both scores exist.
    both = out.home_score.notna() & out.away_score.notna()
    out["score_available"] = both & out.completed.astype(bool)

    # No independent result source is bundled with this checkout.  Champion
    # or series-length comparisons belong in a separately recorded validation
    # job and must not manufacture labels here.
    out.to_parquet(FEAT / "po_corpus.parquet", index=False)
    settled = int(out.winner_team_id.notna().sum())
    print(f"po_corpus: {len(out)} postseason games from a verified source snapshot "
          f"({settled} settled, {int(out.score_available.sum())} with scores) "
          f"-> {FEAT / 'po_corpus.parquet'}")
    print(out[out.completed.astype(bool)]
          .groupby(["season", "round_code"]).size().unstack(fill_value=0).to_string())
    return out


if __name__ == "__main__":
    build_po_corpus()
