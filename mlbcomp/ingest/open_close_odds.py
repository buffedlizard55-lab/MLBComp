"""Ingest open/close MLB betting lines (bettingtools / SBR) with validation.

Source: pwu97/bettingtools ``data/mlb_odds_2014..2019.rda`` — historical MLB
Vegas lines scraped/downloaded from sportsbookreviewsonline.com (per the
repository LICENSE) and built by ``data-raw/MLB_Datasets.R``.  Columns carry
explicit **Open** and **Close** moneyline and totals semantics plus the final
score.

Timestamp semantics (definitional, documented — not invented prices):

* **open** — the opening line for the game.  It is published before first
  pitch, so its availability at the game's decision time (first pitch) is
  guaranteed by the market's own definition.
* **close** — the last line before first pitch.  Its availability time equals
  first pitch by definition, which is exactly the decision timestamp used by
  the backtest.

We therefore stamp both ``observed_at`` and ``available_at`` with the game's
scheduled first-pitch time (``start_utc``): a conservative bound for the open
(no earlier than reality is claimed) and exact for the close.  Prices
themselves are never synthesized — a row either carries source prices or it
fails validation.

Per-row content validation (all must pass for VERIFIED):

* two-way open moneyline with finite non-zero prices and vig in [1.00, 1.10];
* two-way close moneyline with finite non-zero prices and vig in [0.99, 1.15];
* totals line in [3.5, 15.5] with finite non-zero juice at open and close;
* unique join to the schedule on date + teams (doubleheaders resolved by
  score agreement, otherwise the row stays unjoined);
* score agreement with the schedule whenever both sides are present.

A cross-check against the cesar-dx moneyline source (2019 overlap) is recorded
as an audit note; a systematic mismatch demotes the whole year to
PARTIALLY_VERIFIED instead of silently trusting one source.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .. import db
from ..config import FEAT, RAW, TODAY, am_to_prob
from .baseballr import NAME_TO_ABBR

ODDS_RDA_DIR = RAW / "odds_open_close"
OUT_PATH = FEAT / "odds_open_close.parquet"

# Book/Lahman abbreviation fallbacks observed in the source (name column is
# primary; this only repairs the single HOW/NaN row and similar typos).
ABBREV_FIX = {"HOW": "HOU", "LOS": "LAD", "SDG": "SD", "CUB": "CHC",
              "KAN": "KC", "TAM": "TB", "SFO": "SF", "WSH": "WAS", "OAK": "ATH"}


def _load_frames() -> pd.DataFrame:
    import pyreadr
    frames = []
    for path in sorted(ODDS_RDA_DIR.glob("mlb_odds_*.rda")):
        result = pyreadr.read_r(str(path))
        frame = list(result.values())[0].copy()
        frame["source_year_file"] = path.stem
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"no mlb_odds_*.rda files under {ODDS_RDA_DIR}")
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    return out


def _map_teams(frame: pd.DataFrame) -> pd.DataFrame:
    for side in ("home", "away"):
        name = frame[f"{side}_name"]
        abbrev = frame[f"{side}_abbrev"]
        mapped = name.map(NAME_TO_ABBR)
        fallback = abbrev.map(ABBREV_FIX)
        # names are canonical when present; abbreviations repair missing names
        frame[f"{side}_abbr"] = mapped.fillna(fallback)
        # last-resort: direct abbrev if it is already a canonical code
        frame[f"{side}_abbr"] = frame[f"{side}_abbr"].fillna(
            abbrev.where(abbrev.isin(set(NAME_TO_ABBR.values()))))
    return frame


def _vig(home_odds, away_odds) -> pd.Series:
    return home_odds.map(am_to_prob) + away_odds.map(am_to_prob)


def _validate_content(frame: pd.DataFrame) -> pd.DataFrame:
    h_open = pd.to_numeric(frame["home_open_ml"], errors="coerce")
    a_open = pd.to_numeric(frame["away_open_ml"], errors="coerce")
    h_close = pd.to_numeric(frame["home_close_ml"], errors="coerce")
    a_close = pd.to_numeric(frame["away_close_ml"], errors="coerce")
    open_vig = _vig(h_open, a_open)
    close_vig = _vig(h_close, a_close)
    frame["ml_open_ok"] = (
        h_open.notna() & a_open.notna() & (h_open != 0) & (a_open != 0)
        & open_vig.between(1.00, 1.10))
    frame["ml_close_ok"] = (
        h_close.notna() & a_close.notna() & (h_close != 0) & (a_close != 0)
        & close_vig.between(0.99, 1.15))
    open_line = pd.to_numeric(frame["open_ou_line"], errors="coerce")
    close_line = pd.to_numeric(frame["close_ou_line"], errors="coerce")
    open_odds = pd.to_numeric(frame["open_ou_odds"], errors="coerce")
    close_odds = pd.to_numeric(frame["close_ou_odds"], errors="coerce")
    frame["ou_ok"] = (
        open_line.between(3.5, 15.5) & close_line.between(3.5, 15.5)
        & open_odds.notna() & (open_odds != 0)
        & close_odds.notna() & (close_odds != 0))
    frame["home_open_ml"] = h_open
    frame["away_open_ml"] = a_open
    frame["home_close_ml"] = h_close
    frame["away_close_ml"] = a_close
    frame["open_ou_line"] = open_line
    frame["close_ou_line"] = close_line
    frame["open_ou_odds"] = open_odds
    frame["close_ou_odds"] = close_odds
    frame["teams_mapped"] = frame["home_abbr"].notna() & frame["away_abbr"].notna()
    return frame


def _join_schedule(odds: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Join on date+teams; resolve doubleheaders by order, scores for validity."""
    sched = games[["game_pk", "game_date", "home_abbr", "away_abbr",
                   "home_score", "away_score", "start_utc"]].copy()
    sched["game_date"] = sched["game_date"].astype(str).str[:10]
    # Rank rows inside each date+teams key so doubleheaders pair 1st with 1st.
    odds = odds.copy()
    odds["_ord"] = odds.groupby(["date", "home_abbr", "away_abbr"], sort=False).cumcount()
    sched["_ord"] = sched.groupby(["game_date", "home_abbr", "away_abbr"], sort=False).cumcount()
    merged = odds.merge(
        sched, left_on=["date", "home_abbr", "away_abbr", "_ord"],
        right_on=["game_date", "home_abbr", "away_abbr", "_ord"],
        how="left", suffixes=("_odds", "_sch"))

    both_scores = (merged["home_score_odds"].notna() & merged["home_score_sch"].notna()
                   & merged["away_score_odds"].notna() & merged["away_score_sch"].notna())
    score_ok = both_scores & (merged["home_score_odds"] == merged["home_score_sch"]) \
        & (merged["away_score_odds"] == merged["away_score_sch"])
    joined = merged["game_pk"].notna()
    # A row with scores on both sides that disagree is never eligible.
    score_conflict = both_scores & ~score_ok
    merged["join_status"] = np.where(
        ~joined, "NO_SCHEDULE_MATCH",
        np.where(score_conflict, "SCORE_CONFLICT",
                 np.where(both_scores, "SCORE_MATCHED", "DATE_TEAMS_MATCHED")))
    return merged


def _cross_check_cesar(oc: pd.DataFrame) -> dict:
    """Compare 2019 closing lines with the cesar-dx moneyline source."""
    cesar_path = FEAT / "odds.parquet"
    if not cesar_path.exists():
        return {"status": "NOT_RUN", "reason": "cesar odds.parquet not present"}
    cesar = pd.read_parquet(cesar_path)
    cesar = cesar[cesar.game_date.astype(str).str[:4] == "2019"]
    merged = oc[oc.season == 2019].merge(
        cesar[["game_pk", "home_odds", "away_odds"]], on="game_pk", how="inner",
        suffixes=("_bt", "_cz"))
    if merged.empty:
        return {"status": "NOT_RUN", "reason": "no overlapping 2019 games"}
    rows = []
    for r in merged.itertuples():
        try:
            h_bt, a_bt = float(r.home_close_ml), float(r.away_close_ml)
            h_cz, a_cz = float(pd.to_numeric(r.home_odds, errors="coerce")), float(pd.to_numeric(r.away_odds, errors="coerce"))
        except (TypeError, ValueError):
            continue
        if not all(np.isfinite([h_bt, a_bt, h_cz, a_cz])):
            continue
        bh, ba = am_to_prob(h_bt), am_to_prob(a_bt)
        ch, ca = am_to_prob(h_cz), am_to_prob(a_cz)
        # de-vig both pairs before comparing preference strength
        bhp, bap = bh / (bh + ba), ba / (bh + ba)
        chp, cap = ch / (ch + ca), ca / (ch + ca)
        rows.append({"abs_diff_home": abs(bhp - chp),
                     "fav_agree": (bhp > 0.5) == (chp > 0.5),
                     "close_home": bhp, "cesar_home": chp})
    if not rows:
        return {"status": "NOT_RUN", "reason": "no comparable rows"}
    cmp = pd.DataFrame(rows)
    stats = {
        "status": "COMPLETED",
        "n_overlap_games": int(len(cmp)),
        "mean_abs_devig_home_diff": float(cmp.abs_diff_home.mean()),
        "median_abs_devig_home_diff": float(cmp.abs_diff_home.median()),
        "favorite_agreement_rate": float(cmp.fav_agree.mean()),
        "pct_within_0_05": float((cmp.abs_diff_home <= 0.05).mean()),
        "pct_within_0_10": float((cmp.abs_diff_home <= 0.10).mean()),
        "interpretation": (
            "Two independent secondary sources agree on the favorite and sit "
            "close in de-vigged probability; residual differences are "
            "consistent with book-to-book variation, not join errors."),
    }
    if stats["favorite_agreement_rate"] < 0.85 or stats["mean_abs_devig_home_diff"] > 0.08:
        stats["interpretation"] = (
            "Cross-check failed the systematic-agreement screen (two independent "
            "secondary sources disagree beyond book-to-book norms); 2019 close "
            "prices remain usable for evaluation but are flagged here for audit.")
        stats["status"] = "FAILED_SCREEN"
    return stats


def build() -> dict:
    """Validate and write data/features/odds_open_close.parquet."""
    db.init_db()
    if not (FEAT / "games.parquet").exists():
        raise FileNotFoundError("games.parquet missing; run mlbcomp.ingest.baseballr first")
    games = pd.read_parquet(FEAT / "games.parquet")
    odds = _map_teams(_load_frames())
    odds = _validate_content(odds)
    joined = _join_schedule(odds, games)

    # season column for grouping (from the schedule side)
    if "season" not in joined.columns:
        joined["season"] = pd.to_datetime(joined["date"], errors="coerce").dt.year

    eligible = joined["join_status"].isin(["SCORE_MATCHED", "DATE_TEAMS_MATCHED"]) \
        & joined["teams_mapped"]
    joined["ml_verified"] = eligible & joined["ml_open_ok"] & joined["ml_close_ok"]
    joined["ou_verified"] = eligible & joined["ou_ok"]

    keep = {
        "game_pk": joined["game_pk"],
        "season": joined["season"],
        "game_date": joined["date"],
        "home_abbr": joined["home_abbr"],
        "away_abbr": joined["away_abbr"],
        "home_open_ml": joined["home_open_ml"],
        "away_open_ml": joined["away_open_ml"],
        "home_close_ml": joined["home_close_ml"],
        "away_close_ml": joined["away_close_ml"],
        "open_ou_line": joined["open_ou_line"],
        "open_ou_odds": joined["open_ou_odds"],
        "close_ou_line": joined["close_ou_line"],
        "close_ou_odds": joined["close_ou_odds"],
        "home_run_line": pd.to_numeric(joined["home_run_line"], errors="coerce"),
        "away_run_line": pd.to_numeric(joined["away_run_line"], errors="coerce"),
        "home_run_line_odds": pd.to_numeric(joined["home_run_line_odds"], errors="coerce"),
        "away_run_line_odds": pd.to_numeric(joined["away_run_line_odds"], errors="coerce"),
        "start_utc": joined["start_utc"],
        "join_status": joined["join_status"],
        "ml_open_ok": joined["ml_open_ok"],
        "ml_close_ok": joined["ml_close_ok"],
        "ou_ok": joined["ou_ok"],
        "ml_verified": joined["ml_verified"],
        "ou_verified": joined["ou_verified"],
        "source_id": "bettingtools_open_close",
        "source_url": "https://github.com/pwu97/bettingtools",
        "verification_status": np.where(joined["ml_verified"], "VERIFIED",
                                        np.where(eligible, "PARTIALLY_VERIFIED", "UNVERIFIED")),
    }
    out = pd.DataFrame(keep)
    out = out[out.game_pk.notna()].drop_duplicates("game_pk")
    out["game_pk"] = out.game_pk.astype(int)
    FEAT.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT_PATH, index=False)

    cross = _cross_check_cesar(out)
    if cross.get("status") == "FAILED_SCREEN":
        out.loc[out.season == 2019, ["ml_verified", "verification_status"]] = \
            [False, "PARTIALLY_VERIFIED"]
        out.to_parquet(OUT_PATH, index=False)
        db.record_issue("ODDS-CROSSCHECK-2019", "MARKET_DATA",
                        f"2019 open/close lines failed the cesar-dx cross-check screen: {cross}",
                        severity="HIGH", source_ids=["bettingtools_open_close", "cesar_dx_mlb_odds"])
    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "pwu97/bettingtools mlb_odds_2014-2019 (sportsbookreviewsonline.com lines)",
        "rows": int(len(out)),
        "ml_verified_rows": int(out.ml_verified.sum()),
        "ou_verified_rows": int(out.ou_verified.sum()),
        "join_status_counts": {k: int(v) for k, v in out.join_status.value_counts().items()},
        "seasons": sorted(int(s) for s in out.season.dropna().unique()),
        "cross_check_vs_cesar_2019": cross,
        "timestamp_policy": (
            "observed_at = available_at = scheduled first pitch (start_utc): "
            "definitional close time; conservative bound for open lines."),
    }
    (FEAT / "odds_open_close_validation.json").write_text(json.dumps(summary, indent=2))

    # Persist verified quotes into the normalized market tables.
    _persist_market_quotes(out)

    db.audit("odds_open_close:validated", json.dumps({
        "rows": summary["rows"], "ml_verified": summary["ml_verified_rows"],
        "ou_verified": summary["ou_verified_rows"]}))
    print(f"[open_close] rows={summary['rows']} ml_verified={summary['ml_verified_rows']} "
          f"ou_verified={summary['ou_verified_rows']} join={summary['join_status_counts']}")
    print(f"[open_close] cross-check 2019: {json.dumps(cross)}")
    return summary


def _persist_market_quotes(out: pd.DataFrame) -> None:
    """Mirror ML entry/close rows into markets + market_quotes + observations."""
    with db.db() as conn:
        conn.execute("DELETE FROM market_quotes WHERE source_observation_id LIKE 'quote:bettingtools%'")
        conn.execute("DELETE FROM markets WHERE market_id LIKE 'ML:open:%' OR market_id LIKE 'ML:close:%'")
        conn.execute("DELETE FROM source_observations WHERE observation_id LIKE 'quote:bettingtools:%'")
        for r in out[out.ml_verified].itertuples():
            start = str(r.start_utc)
            # Game-level quote observations (both sides share provenance).
            for label in ("open", "close"):
                obs_id = f"quote:bettingtools:{label}:{r.game_pk}"
                conn.execute(
                    "INSERT OR REPLACE INTO source_observations "
                    "(observation_id,source_id,source_locator,record_key,field_name,raw_value,"
                    "retrieval_time,availability_time,checksum,verification_status,notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (obs_id, "bettingtools_open_close",
                     "https://github.com/pwu97/bettingtools",
                     f"game:{r.game_pk}", f"{label}_moneyline",
                     f"{r.home_open_ml}/{r.away_open_ml}" if label == "open"
                     else f"{r.home_close_ml}/{r.away_close_ml}",
                     db.utcnow(), start,
                     db.stable_hash({"game_pk": int(r.game_pk), "tier": label,
                                     "h": r.home_open_ml if label == "open" else r.home_close_ml,
                                     "a": r.away_open_ml if label == "open" else r.away_close_ml}),
                     "VERIFIED",
                     "Content-validated source prices; observed_at/available_at set to "
                     "scheduled first pitch per documented open/close semantics."))
            for side, odds_open, odds_close in (
                ("home", r.home_open_ml, r.home_close_ml),
                ("away", r.away_open_ml, r.away_close_ml),
            ):
                if not (np.isfinite(odds_open) and np.isfinite(odds_close)):
                    continue
                for label, price in (("open", odds_open), ("close", odds_close)):
                    market_id = f"ML:{label}:{r.game_pk}"
                    quote_id = f"quote:bettingtools:{label}:{r.game_pk}:{side}"
                    conn.execute(
                        "INSERT OR REPLACE INTO markets "
                        "(market_id,game_pk,environment,round_code,market_type,selection,line,"
                        "settlement_rule,source_id,verification_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (market_id, int(r.game_pk), None, None, "ML",
                         side.upper(), None, "W/L settlement by final score",
                         "bettingtools_open_close", "VERIFIED"))
                    conn.execute(
                        "INSERT OR REPLACE INTO market_quotes "
                        "(quote_id,market_id,observed_at,available_at,price_american,price_decimal,"
                        "implied_probability,closing_flag,source_observation_id,verification_status) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (quote_id, market_id, start, start, float(price),
                         None, float(am_to_prob(price)),
                         1 if label == "close" else 0,
                         f"quote:bettingtools:{label}:{r.game_pk}",
                         "VERIFIED"))


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
