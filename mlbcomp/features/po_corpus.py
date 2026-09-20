"""Verified postseason corpus (P1 + P2).

P1  = 2025 postseason from the mirror (reality spot-checked: matchups,
      series scores, and key game scores match documented results).
      Full scores available -> usable for ML AND totals strategies.
P2  = 2019-2024 reconstructed game winners (see
      mlbcomp/data_recon/po_results.py for provenance and confidence).
      Scores are None -> usable for ML settlement only, NOT for totals
      or run-environment research.

The mirror's own 2019-2024 postseason rows are EXCLUDED (fabricated).
2015-2018 are series-level supporting evidence only (not in this corpus).
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from ..config import FEAT
from ..data_recon.po_results import RECON, G1_HOST, WC1_HOST, standard_series_dates

ABB_FIX = {"WSH": "WAS", "OAK": "ATH"}


def _canon(a: str) -> str:
    return ABB_FIX.get(a, a)


def _home_away_pattern(rnd: str, n_games: int, g1_host: str, winner: str,
                       runner_up: str) -> list[tuple[str, str]]:
    """home/away abbr per game slot following the standard format."""
    other = runner_up if g1_host == winner else winner
    n_need = 4 if rnd in ("LCS", "WS") else (3 if rnd == "DS" and n_games >= 4 else 2)
    home_at = set()
    for i in range(1, n_games + 1):
        if rnd == "WC":
            home_at.add(i) if (i <= 2 or n_games == 1) else None
        elif rnd == "DS" and n_games >= 4:  # BO5
            home_at.add(i) if i in (1, 2, 5) else None
        elif rnd == "DS":  # BO3 (2020)
            home_at.add(i)
        else:  # BO7
            home_at.add(i) if i in (1, 2, 6, 7) else None
    out = []
    for i in range(1, n_games + 1):
        if i in home_at:
            out.append((g1_host, other))
        else:
            out.append((other, g1_host))
    return out


def build_po_corpus() -> pd.DataFrame:
    conn = sqlite3.connect(FEAT.parent / "mlbcomp.db")
    teams = pd.read_sql("SELECT * FROM teams", conn)
    conn.close()
    ab2id = {_canon(r.abbr): int(r.team_id) for r in teams.itertuples()}

    # ---------- P1: 2025 from mirror ----------
    g = pd.read_parquet(FEAT / "games.parquet")
    po = g[g.round_code.notna()].copy()
    p1 = po[po.season == 2025][["game_pk", "season", "game_date", "round_code",
                                 "home_team_id", "away_team_id", "home_score",
                                 "away_score", "home_abbr", "away_abbr",
                                 "winner_team_id", "league"]].copy()
    p1["provenance"] = "mirror_verified_2025"
    p1["confidence"] = "high"
    p1["score_available"] = True
    p1["source"] = "baseballr-data mirror (reality spot-checked)"

    # ---------- P2: reconstructed 2019-2024 ----------
    rows = []
    idx = 0
    for yr in (2019, 2020, 2021, 2022, 2023, 2024):
        d = RECON[yr]
        for (rnd, lg, winner, runner_up, game_winners, conf) in d["series"]:
            n = len(game_winners)
            if yr == 2020:
                # Orlando bubble: no real home parks; 'home' is a
                # placeholder (no home advantage applies in 2020 PO).
                host = winner
            elif rnd == "WC" and n == 1:
                host = WC1_HOST[yr][winner]
            else:
                host = G1_HOST[yr][rnd][winner]
            anchor = d["anchor"][rnd]
            dates = standard_series_dates(anchor, rnd, n)
            pattern = _home_away_pattern(rnd, n, host, winner, runner_up)
            for gi, (w, (h, a), dt) in enumerate(zip(game_winners, pattern, dates)):
                idx += 1
                rows.append({
                    "game_pk": -int(yr * 1000 + idx),
                    "season": yr,
                    "game_date": dt,
                    "round_code": rnd,
                    "home_abbr": _canon(h),
                    "away_abbr": _canon(a),
                    "home_team_id": ab2id[_canon(h)],
                    "away_team_id": ab2id[_canon(a)],
                    "home_score": None,
                    "away_score": None,
                    "winner_team_id": ab2id[_canon(w)],
                    "league": lg if lg != "WS" else "WS",
                    "provenance": "reconstructed_winners",
                    "confidence": conf,
                    "score_available": False,
                    "source": "manual reconstruction from documented public results",
                })
    p2 = pd.DataFrame(rows)
    p2["home_score"] = pd.to_numeric(p2["home_score"], errors="coerce")
    p2["away_score"] = pd.to_numeric(p2["away_score"], errors="coerce")

    out = pd.concat([p1, p2], ignore_index=True)
    out["series_key"] = [
        f"R{int(s)}:{r}:{l}:{min(int(a), int(b))}-{max(int(a), int(b))}"
        for s, r, l, a, b in zip(out.season, out.round_code, out.league,
                                 out.home_team_id, out.away_team_id)]
    # keep the real mirror series_key for the 2025 P1 games
    conn2 = sqlite3.connect(FEAT.parent / "mlbcomp.db")
    mser = pd.read_sql("SELECT game_pk, series_key FROM games", conn2)
    conn2.close()
    real = mser[mser.game_pk.isin(p1.game_pk)].set_index("game_pk").series_key
    out["series_key"] = [real.get(pk, sk) for pk, sk in
                         zip(out.game_pk, out["series_key"])]
    out.to_parquet(FEAT / "po_corpus.parquet", index=False)
    print(f"po_corpus: {len(out)} games "
          f"(P1 2025: {len(p1)}, P2 reconstructed: {len(p2)}) "
          f"-> {FEAT / 'po_corpus.parquet'}")
    print(out.groupby(["season", "round_code"]).size().unstack(fill_value=0).to_string())
    return out


if __name__ == "__main__":
    build_po_corpus()
