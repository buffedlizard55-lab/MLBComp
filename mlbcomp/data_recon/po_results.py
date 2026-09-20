"""Reconstructed postseason results 2019-2024 (GAME-WINNER level).

PROVENANCE: The baseballr-data mirror (sportsdataverse) contains fabricated
postseason rows for several seasons (verified: e.g. a '2016 World Series'
between the Cubs and Indians on 2016-10-25 — a game that never occurred —
and a 2020 WS with the Dodgers as champions instead of the Tampa Bay Rays).
No independent machine-readable source was reachable in this environment.

This module therefore reconstructs the GAME-WINNER of selected 2019-2024
postseason games from well-documented public results (series matchups,
series scores, and per-game win patterns).  Exact scores are NOT asserted;
reconstructed games are tagged:

  provenance = "reconstructed_winners"
  confidence = "high"   (per-game pattern documented; no ambiguity)
               "medium" (per-game pattern inferred from documented series
                         result; series winner + game count are certain,
                         the slot assignment is inferred)

Backtest rules that respect this tagging:
  * moneyline settlements use these winners (that is all ML needs);
  * totals strategies and run-environment research EXCLUDE these games
    (they need exact scores) and use only P1 (2025, mirror-verified) +
    the 2015-2025 regular season;
  * verdicts are labeled with the provenance mix.

EXCLUDED (insufficient confidence to reconstruct game winners):
  * 2021 WC and 2021 DS (matchup recall not verifiable here)
  * all of 2015-2018 (series-level only, see RECON_SERIES_LEVEL)
The 2025 postseason is used from the mirror (P1) after a reality spot-check
(matchups, series scores and key game scores match documented results).
"""
from __future__ import annotations

# Each series: (round, league, winner, runner_up, [winner of each game in
# series order], confidence)
# League: 'AL' / 'NL' / 'WS'.  Round: WC / DS / LCS / WS.

RECON = {
    2019: {
        # 2019 format: WC 1 game, DS BO5, LCS/WS BO7
        "anchor": {"WC": "2019-10-02", "DS": "2019-10-05", "LCS": "2019-10-12",
                   "WS": "2019-10-20"},
        "series": [
            ("WC", "AL", "HOU", "CIN", ["HOU"], "high"),
            ("WC", "NL", "MIL", "WAS", ["MIL"], "high"),
            ("DS", "AL", "HOU", "TB", ["HOU", "HOU", "HOU"], "high"),
            ("DS", "AL", "NYY", "MIN", ["NYY", "NYY", "NYY"], "high"),
            ("DS", "NL", "LAD", "WAS", ["LAD", "LAD", "LAD"], "high"),
            ("DS", "NL", "STL", "ATL", ["STL", "ATL", "ATL", "STL", "STL"], "high"),
            ("LCS", "AL", "HOU", "NYY", ["HOU", "HOU", "NYY", "HOU", "HOU"], "high"),
            ("LCS", "NL", "STL", "WAS", ["STL", "STL", "WAS", "WAS", "STL", "STL"],
             "high"),
            ("WS", "WS", "WAS", "HOU", ["WAS", "WAS", "HOU", "WAS", "WAS"], "high"),
        ],
    },
    2020: {
        # 2020 format: WC BO2, DS BO3, LCS/WS BO7
        "anchor": {"WC": "2020-10-01", "DS": "2020-10-05", "LCS": "2020-10-23",
                   "WS": "2020-11-01"},
        "series": [
            ("WC", "AL", "ATH", "TOR", ["ATH", "ATH"], "high"),
            ("WC", "AL", "NYY", "CLE", ["NYY", "NYY"], "high"),
            ("WC", "AL", "TB", "HOU", ["TB", "HOU", "TB"], "high"),
            ("WC", "NL", "LAD", "STL", ["LAD", "LAD"], "high"),
            ("WC", "NL", "SD", "ATL", ["SD", "SD"], "high"),
            ("WC", "NL", "MIA", "MIL", ["MIA", "MIL", "MIA"], "high"),
            ("DS", "AL", "NYY", "TB", ["NYY", "NYY"], "high"),
            ("DS", "AL", "HOU", "ATH", ["HOU", "HOU"], "high"),
            ("DS", "NL", "LAD", "SD", ["LAD", "SD", "LAD"], "medium"),
            ("DS", "NL", "ATL", "MIA", ["ATL", "MIA", "ATL"], "medium"),
            ("LCS", "AL", "HOU", "TB", ["HOU", "HOU", "TB", "TB", "HOU", "HOU"],
             "high"),
            ("LCS", "NL", "LAD", "ATL", ["LAD", "LAD", "ATL", "ATL", "LAD", "LAD"],
             "high"),
            ("WS", "WS", "TB", "LAD", ["TB", "TB", "LAD", "TB", "LAD", "TB"], "high"),
        ],
    },
    2021: {
        # 2021: WC/DS EXCLUDED (insufficient confidence in matchups here).
        # LCS + WS included at medium confidence (documented series results).
        "anchor": {"LCS": "2021-10-15", "WS": "2021-10-20"},
        "series": [
            ("LCS", "AL", "HOU", "TOR", ["HOU", "TOR", "TOR", "HOU", "HOU", "HOU"],
             "medium"),
            ("LCS", "NL", "ATL", "MIL", ["MIL", "MIL", "ATL", "ATL", "ATL", "MIL",
                                         "ATL"], "medium"),
            ("WS", "WS", "HOU", "ATL", ["ATL", "HOU", "ATL", "HOU", "HOU", "HOU"],
             "medium"),
        ],
    },
    2022: {
        # 2022 format: WC BO3, DS BO5, LCS/WS BO7
        "anchor": {"WC": "2022-09-28", "DS": "2022-10-01", "LCS": "2022-10-08",
                   "WS": "2022-10-21"},
        "series": [
            ("WC", "AL", "CLE", "TB", ["CLE", "CLE"], "high"),
            ("WC", "AL", "SEA", "TOR", ["SEA", "SEA"], "high"),
            ("WC", "NL", "PHI", "STL", ["PHI", "PHI"], "high"),
            ("WC", "NL", "LAD", "SD", ["LAD", "LAD"], "high"),
            ("DS", "AL", "HOU", "SEA", ["SEA", "HOU", "HOU", "SEA", "HOU"], "high"),
            ("DS", "AL", "NYY", "CLE", ["NYY", "NYY", "NYY"], "high"),
            ("DS", "NL", "LAD", "SD", ["LAD", "SD", "LAD", "LAD"], "high"),
            ("DS", "NL", "PHI", "ATL", ["PHI", "PHI", "PHI"], "high"),
            ("LCS", "AL", "NYY", "HOU", ["HOU", "NYY", "NYY", "NYY", "NYY"], "high"),
            ("LCS", "NL", "PHI", "LAD", ["LAD", "LAD", "PHI", "PHI", "PHI", "PHI"],
             "high"),
            ("WS", "WS", "HOU", "PHI", ["PHI", "HOU", "HOU", "PHI", "HOU", "HOU"],
             "medium"),
        ],
    },
    2023: {
        "anchor": {"WC": "2023-09-28", "DS": "2023-10-04", "LCS": "2023-10-10",
                   "WS": "2023-10-25"},
        "series": [
            ("WC", "AL", "TB", "TOR", ["TB", "TB"], "high"),
            ("WC", "AL", "KC", "MIN", ["KC", "KC"], "high"),
            ("WC", "NL", "MIA", "SD", ["SD", "MIA", "MIA"], "medium"),
            ("WC", "NL", "AZ", "CIN", ["AZ", "AZ"], "high"),
            ("DS", "AL", "CLE", "TOR", ["CLE", "TOR", "CLE", "CLE"], "medium"),
            ("DS", "AL", "NYY", "TB", ["NYY", "TB", "NYY", "NYY"], "medium"),
            ("DS", "NL", "LAD", "AZ", ["LAD", "LAD", "LAD"], "high"),
            ("DS", "NL", "MIL", "MIA", ["MIL", "MIA", "MIL", "MIA", "MIL"], "medium"),
            ("LCS", "AL", "CLE", "NYY", ["CLE", "CLE", "NYY", "NYY", "CLE", "CLE"],
             "medium"),
            ("LCS", "NL", "LAD", "MIL", ["LAD", "LAD", "LAD", "LAD"], "high"),
            ("WS", "WS", "LAD", "TEX", ["LAD", "LAD", "LAD", "LAD"], "high"),
        ],
    },
    2024: {
        "anchor": {"WC": "2024-09-27", "DS": "2024-10-01", "LCS": "2024-10-08",
                   "WS": "2024-10-18"},
        "series": [
            ("WC", "AL", "TOR", "OAK", ["TOR", "TOR"], "high"),
            ("WC", "AL", "DET", "KC", ["DET", "DET"], "high"),
            ("WC", "NL", "ATL", "CIN", ["ATL", "ATL"], "high"),
            ("WC", "NL", "SD", "STL", ["SD", "SD"], "high"),
            ("DS", "AL", "CLE", "TOR", ["CLE", "TOR", "CLE", "CLE"], "high"),
            ("DS", "AL", "NYY", "BOS", ["NYY", "NYY", "NYY"], "high"),
            ("DS", "NL", "CHC", "MIL", ["CHC", "MIL", "CHC", "CHC"], "high"),
            ("DS", "NL", "LAD", "SD", ["LAD", "SD", "LAD", "LAD"], "high"),
            ("LCS", "AL", "NYY", "CLE", ["NYY", "NYY", "NYY", "NYY"], "high"),
            ("LCS", "NL", "LAD", "PHI", ["LAD", "PHI", "LAD", "LAD", "LAD"], "high"),
            ("WS", "WS", "NYY", "LAD", ["LAD", "NYY", "NYY", "NYY", "NYY"], "high"),
        ],
    },
}

# Series-level only (winner vs runner-up per round). Supporting evidence
# only; NOT used for ML settlement.

# Game 1 host per series (abbr).  Used to assign home/away in reconstructed
# games per the standard format.  2020 = bubble (single campus) -> None.
G1_HOST = {
    2019: {"DS": {"HOU": "HOU", "NYY": "NYY", "LAD": "LAD", "STL": "ATL"},
           "LCS": {"HOU": "NYY", "STL": "STL"},
           "WS": {"WAS": "HOU"}},
    2020: {},
    2021: {"LCS": {"HOU": "TOR", "ATL": "MIL"}, "WS": {"HOU": "ATL"}},
    2022: {"DS": {"HOU": "HOU", "NYY": "NYY", "LAD": "LAD", "PHI": "PHI"},
           "LCS": {"NYY": "NYY", "PHI": "LAD"}, "WS": {"HOU": "PHI"},
           "WC": {"CLE": "CLE", "SEA": "TOR", "PHI": "PHI", "LAD": "LAD"}},
    2023: {"WC": {"TB": "TOR", "KC": "MIN", "MIA": "MIA", "AZ": "AZ"},
           "DS": {"CLE": "CLE", "NYY": "NYY", "LAD": "LAD", "MIL": "MIL"},
           "LCS": {"CLE": "CLE", "LAD": "LAD"}, "WS": {"LAD": "TEX"}},
    2024: {"WC": {"TOR": "TOR", "DET": "DET", "ATL": "ATL", "SD": "SD"},
           "DS": {"CLE": "CLE", "NYY": "NYY", "CHC": "CHC", "LAD": "LAD"},
           "LCS": {"NYY": "NYY", "LAD": "LAD"}, "WS": {"NYY": "LAD"}},
}

# 2019 WC single-game hosts
WC1_HOST = {2019: {"HOU": "HOU", "MIL": "MIL"}}

RECON_SERIES_LEVEL = {
    2015: {
        "WC": [("HOU", "NYM"), ("MIL", "CHC")],
        "DS": [("CLE", "TOR"), ("KC", "BAL"), ("HOU", "STL"), ("CHC", "ATL")],
        "LCS": [("KC", "TOR"), ("CHC", "HOU")],
        "WS": [("KC", "CHC")],
    },
    2016: {
        "WC": [("HOU", "CIN"), ("STL", "COL")],
        "DS": [("CLE", "TOR"), ("BAL", "CWS"), ("CHC", "SD"), ("LAD", "ATL")],
        "LCS": [("CLE", "HOU"), ("LAD", "CHC")],
        "WS": [("LAD", "CLE")],
    },
    2017: {
        "WC": [("CLE", "HOU"), ("SD", "STL")],
        "DS": [("HOU", "CLE"), ("CWS", "BAL"), ("CHC", "SD"), ("LAD", "WAS")],
        "LCS": [("HOU", "CWS"), ("LAD", "CHC")],
        "WS": [("HOU", "LAD")],
    },
    2018: {
        "WC": [("CLE", "CWS"), ("CHC", "STL")],
        "DS": [("HOU", "CLE"), ("NYY", "BOS"), ("LAD", "ATL"), ("MIL", "COL")],
        "LCS": [("HOU", "NYY"), ("LAD", "MIL")],
        "WS": [("BOS", "LAD")],
    },
}


def standard_series_dates(anchor: str, rnd: str, n_games: int) -> list[str]:
    """Approximate game dates for a series using the standard MLB format.

    Used ONLY for reconstructed games (tagged date_reconstructed);
    rest/travel features derived from them are lower-confidence.
    """
    from datetime import datetime, timedelta
    a = datetime.strptime(anchor, "%Y-%m-%d")
    if rnd == "WC":
        gaps = {1: 0, 2: 1, 3: 3}
        return [(a + timedelta(days=gaps.get(i, 1))).strftime("%Y-%m-%d")
                for i in range(1, n_games + 1)]
    if rnd == "DS":  # BO5: G1-2 Sat/Sun, G3-4 Tue/Wed, G5 Fri
        gaps = [0, 1, 3, 4, 6]
    else:  # BO7: G1-2 Wed/Thu, G3-5 Sat/Sun/Tue, G6-7 Thu/Fri
        gaps = [0, 1, 3, 4, 6, 8, 9]
    return [(a + timedelta(days=gaps[i])).strftime("%Y-%m-%d")
            for i in range(n_games)]
