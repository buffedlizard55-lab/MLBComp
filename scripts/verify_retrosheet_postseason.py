"""Independent cross-source verification of MLB Postseason games (2015-2025).

Compares postseason game results from the Retrosheet official gamelogs:
- GLWC.TXT (Wild Card)
- GLDV.TXT (Division Series)
- GLLC.TXT (League Championship Series)
- GLWS.TXT (World Series)
mirror: github.com/chadwickbureau/retrosheet via api.github.com blobs.

Validates that:
1. Total postseason games 2015-2025 is exactly 440 games.
2. Round distribution matches: WC: 67, DS: 181, LCS: 126, WS: 66.
3. Every postseason wager in data/bets_ledger.json maps to an authentic game.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RETRO_ROUNDS = {
    "GLWC": "WC",
    "GLDV": "DS",
    "GLLC": "LCS",
    "GLWS": "WS",
}

RETRO_TO_MLB_ABBR = {
    "ANA": "LAA",
    "ARI": "AZ",
    "BAL": "BAL",
    "BOS": "BOS",
    "CHA": "CWS",
    "CHN": "CHC",
    "CIN": "CIN",
    "CLE": "CLE",
    "COL": "COL",
    "DET": "DET",
    "HOU": "HOU",
    "KCA": "KC",
    "LAN": "LAD",
    "MIA": "MIA",
    "MIL": "MIL",
    "MIN": "MIN",
    "NYA": "NYY",
    "NYN": "NYM",
    "OAK": "ATH",
    "PHI": "PHI",
    "PIT": "PIT",
    "SDN": "SD",
    "SEA": "SEA",
    "SFN": "SF",
    "SLN": "STL",
    "TBA": "TB",
    "TEX": "TEX",
    "TOR": "TOR",
    "WAS": "WAS",
}


def fetch_retrosheet_games(min_season: int = 2015, max_season: int = 2025) -> list[dict]:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github.raw", "User-Agent": "mlbcomp-retro-verifier"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    all_games = []
    for file_prefix, round_code in RETRO_ROUNDS.items():
        url = f"https://api.github.com/repos/chadwickbureau/retrosheet/contents/gamelog/{file_prefix}.TXT"
        req = urllib.request.Request(url, headers=headers)
        raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
        reader = csv.reader(io.StringIO(raw))
        for row in reader:
            if not row or len(row) < 12:
                continue
            date_str = row[0]
            if len(date_str) < 8 or not date_str[:4].isdigit():
                continue
            season = int(date_str[:4])
            if season < min_season or season > max_season:
                continue
            game_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            away_team_retro = row[3]
            home_team_retro = row[6]
            away_score = int(row[9])
            home_score = int(row[10])
            all_games.append({
                "season": season,
                "game_date": game_date,
                "round_code": round_code,
                "away_team_retro": away_team_retro,
                "home_team_retro": home_team_retro,
                "away_abbr": RETRO_TO_MLB_ABBR.get(away_team_retro, away_team_retro),
                "home_abbr": RETRO_TO_MLB_ABBR.get(home_team_retro, home_team_retro),
                "away_score": away_score,
                "home_score": home_score,
                "winner": "HOME" if home_score > away_score else "AWAY",
            })
    return all_games


def verify_against_ledger() -> dict:
    ledger_path = ROOT / "data" / "bets_ledger.json"
    if not ledger_path.exists():
        raise FileNotFoundError(f"Missing {ledger_path}")

    retro_games = fetch_retrosheet_games(2015, 2025)
    counts = {}
    for g in retro_games:
        counts[g["round_code"]] = counts.get(g["round_code"], 0) + 1

    expected_counts = {"WC": 67, "DS": 181, "LCS": 126, "WS": 66}
    assert counts == expected_counts, f"Retrosheet round counts mismatch: {counts} vs {expected_counts}"
    assert len(retro_games) == 440, f"Expected 440 postseason games, got {len(retro_games)}"

    # Match against ledger
    ledger = json.loads(ledger_path.read_text())
    po_bets = [r for r in ledger if r.get("round_code") and r.get("result") in ["W", "L"]]
    unique_po_games = {r["game_pk"]: r for r in po_bets}

    retro_keys = {(g["game_date"], g["round_code"]): g for g in retro_games}
    matched_games = 0
    for gpk, row in unique_po_games.items():
        dt = row.get("game_date")
        rc = row.get("round_code")
        if (dt, rc) in retro_keys:
            matched_games += 1

    assert matched_games == len(unique_po_games), (
        f"Mismatch: only {matched_games}/{len(unique_po_games)} unique postseason games in ledger matched Retrosheet"
    )

    report = {
        "status": "PASS",
        "retrosheet_source": "https://github.com/chadwickbureau/retrosheet",
        "total_po_games_2015_2025": len(retro_games),
        "po_games_by_round": counts,
        "unique_po_games_in_ledger": len(unique_po_games),
        "matched_games": matched_games,
        "match_rate": 1.0,
    }
    return report


if __name__ == "__main__":
    report = verify_against_ledger()
    print("Retrosheet Cross-Source Postseason Verification PASSED:")
    print(json.dumps(report, indent=2))
