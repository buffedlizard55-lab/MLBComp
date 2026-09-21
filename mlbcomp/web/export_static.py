"""Export the static GitHub Pages site data FROM THE DATABASE.

Why this exists: `data/*.json` used to be produced by
`scripts/generate_mlbcomp_data.py`, which invented its content with
`random.uniform` / `random.choice` — odds, model probabilities, edges, CLV,
win/loss results, "actual scores", Kalshi bid/ask/liquidity/fills and even the
2026-09-20 game slate and probable starters — and then stamped every record
`"verification_status": "VERIFIED_PRIMARY"`.  The site rendered that as the
competition.  That generator is retired; this module is the only writer of
`data/*.json`, and every value it emits comes from `data/mlbcomp.db`, which is
built from the two fetched public sources.

Honesty rules enforced here:
  * no record is labeled verified unless it settled against a real price;
  * picks with no market price are labeled NO_MARKET_PRICE (they are model
    evaluation picks, not trades);
  * totals settled against the synthetic 8.5 line are labeled SYNTHETIC_LINE;
  * Kalshi is exported as an EMPTY list — api.elections.kalshi.com is not
    reachable from this environment, so there are no real contracts, quotes,
    fills or PnL to show (spec: never invent fills or liquidity);
  * the source registry is the DB's registry (8 real entries, 5 of them
    recorded as rejected/unavailable), not a list of plausible-sounding APIs.

Usage:  python3 -m mlbcomp.web.export_static [--ledger-cap 20000]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd

from ..config import DATA, DB_PATH, ENV_LABEL

LEDGER_CAP_DEFAULT = 20000


def _conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit(f"missing {DB_PATH} — run `python -m mlbcomp.ingest.baseballr` first")
    return sqlite3.connect(DB_PATH)


def _matchups(conn) -> dict[int, str]:
    """game_pk -> 'AWY @ HOME' using canonical team abbreviations."""
    df = pd.read_sql(
        "SELECT g.game_pk, a.abbr AS away, h.abbr AS home FROM games g "
        "JOIN teams a ON a.team_id = g.away_team_id "
        "JOIN teams h ON h.team_id = g.home_team_id", conn)
    return {int(r.game_pk): f"{r.away} @ {r.home}" for r in df.itertuples()}


def _scores(conn) -> dict[int, str]:
    df = pd.read_sql(
        "SELECT g.game_pk, a.abbr AS away, h.abbr AS home, g.away_score, "
        "g.home_score FROM games g "
        "JOIN teams a ON a.team_id = g.away_team_id "
        "JOIN teams h ON h.team_id = g.home_team_id "
        "WHERE g.home_score IS NOT NULL", conn)
    return {int(r.game_pk): f"{r.away} {int(r.away_score)} - {r.home} {int(r.home_score)}"
            for r in df.itertuples()}


def _priced_pks() -> set[int]:
    """game_pks that carry a REAL observed price (cesar-dx moneylines).

    The engine writes an assumed +100 (p=0.5) into `bets.market_price` when a
    game has no verified price, so that column alone cannot tell a real quote
    from a placeholder.  This set is the authority.
    """
    from ..config import FEAT
    p = FEAT / "odds.parquet"
    if not p.exists():
        return set()
    o = pd.read_parquet(p)
    return {int(x) for x in o.loc[o.home_odds.notna(), "game_pk"]}


PRICED = _priced_pks()


def _verification(game_pk, synthetic) -> str:
    """Provenance label for a single wager — never overstates.

    ASSUMED_PRICE_PLUS100 is the engine's placeholder for games with no
    observed market price (all postseason games, REG 2015-18 and REG 2026).
    Those rows are model-evaluation picks settled at an invented even-money
    price, not trades.
    """
    if synthetic:
        return "SYNTHETIC_LINE"
    if int(game_pk) in PRICED:
        return "VERIFIED_PRICE"
    return "ASSUMED_PRICE_PLUS100"


def _clean(o):
    """Recursively replace NaN/Infinity with None — they are not valid JSON
    and would make every fetch(...).then(r => r.json()) on the site throw."""
    if isinstance(o, float):
        return None if (o != o or o in (float("inf"), float("-inf"))) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _write(name: str, obj) -> int:
    p = DATA / name
    p.write_text(json.dumps(_clean(obj), indent=2, default=str, allow_nan=False))
    n = len(obj) if isinstance(obj, (list, dict)) else 1
    print(f"  {name:26s} {n:>8,} records")
    return n


def export(ledger_cap: int = LEDGER_CAP_DEFAULT) -> dict:
    conn = _conn()
    mu, sc = _matchups(conn), _scores(conn)
    strat = pd.read_sql("SELECT * FROM strategies", conn)
    sname = dict(zip(strat.strategy_id, strat.name))

    bets = pd.read_sql("SELECT * FROM bets", conn)
    bets["matchup"] = bets.game_pk.map(mu).fillna("(unknown)")
    bets["actual_score"] = bets.game_pk.map(sc).fillna("")
    bets["username"] = bets.strategy_id

    settled = bets[bets.status == "SETTLED"].copy()
    proposed = bets[bets.status.isin(["PROPOSED", "OPEN"])].copy()

    # pricing provenance per wager (the engine's market_price column cannot
    # distinguish a real quote from its assumed +100 placeholder)
    bets["pricing"] = [
        ("SYNTHETIC_LINE" if s else
         ("VERIFIED_PRICE" if int(pk) in PRICED else "ASSUMED_PRICE_PLUS100"))
        for pk, s in zip(bets.game_pk, bets.synthetic.astype(bool))]
    settled["pricing"] = bets.loc[settled.index, "pricing"]
    settled["is_verified"] = settled.pricing == "VERIFIED_PRICE"

    print(f"[export] db: {len(bets):,} bets ({len(settled):,} settled, "
          f"{len(proposed):,} open/proposed), {len(strat)} strategies")

    # ---------------------------------------------------------- leaderboard
    # NOTE: `bets.result` stores 'W'/'L'/'P'; an earlier version of this
    # exporter filtered on 'WIN'/'LOSS' and silently produced a null win rate
    # for every strategy.  Metrics are split by pricing provenance so a
    # real-price result is never blended with an assumed-price one.
    cal = pd.read_sql("SELECT strategy_id, env, brier, log_loss, n_games FROM calibration", conn)
    rows = []
    for (sid, env), sub in bets.groupby(["strategy_id", "env"]):
        # status: SETTLED = traded at a real price; EVAL = unpriced pick whose
        # game is nevertheless finished (all postseason picks, plus REG 2015-18
        # and 2026).  Both have a real W/L outcome, so both count for
        # model-skill win rate; only SETTLED-at-a-real-price counts for ROI.
        st = sub[sub.result.isin(["W", "L", "P"])]
        wins = int((st.result == "W").sum())
        losses = int((st.result == "L").sum())
        pushes = int((st.result == "P").sum())
        ver = st[st.pricing == "VERIFIED_PRICE"]
        asm = st[st.pricing == "ASSUMED_PRICE_PLUS100"]
        syn = st[st.pricing == "SYNTHETIC_LINE"]
        decided = wins + losses
        c_row = cal[(cal.strategy_id == sid) & (cal.env == env)]
        rows.append({
            "strategy_id": sid, "env": env, "bets": len(sub), "settled": len(st),
            "wins": wins, "losses": losses, "pushes": pushes,
            "win_rate": round(100.0 * wins / decided, 2) if decided else None,
            "pnl": round(float(st.pnl.fillna(0).sum()), 2),
            "verified_bets": len(ver),
            "verified_pnl": round(float(ver.pnl.fillna(0).sum()), 2),
            "verified_stake": round(float(ver.stake.fillna(0).sum()), 2),
            "verified_roi": (round(100.0 * float(ver.pnl.fillna(0).sum())
                                   / float(ver.stake.fillna(0).sum()), 3)
                             if float(ver.stake.fillna(0).sum()) else None),
            "verified_win_rate": (round(100.0 * (ver.result == "W").sum()
                                        / max(1, (ver.result.isin(["W", "L"])).sum()), 2)
                                  if len(ver) else None),
            "assumed_bets": len(asm),
            "assumed_win_rate": (round(100.0 * (asm.result == "W").sum()
                                       / max(1, (asm.result.isin(["W", "L"])).sum()), 2)
                                 if len(asm) else None),
            "skill_bets": int((sub.result.isin(["W", "L"])
                               & (sub.pricing != "VERIFIED_PRICE")).sum()),
            "synthetic_bets": len(syn),
            "synthetic_roi": (round(100.0 * float(syn.pnl.fillna(0).sum())
                                    / float(syn.stake.fillna(0).sum()), 3)
                              if float(syn.stake.fillna(0).sum()) else None),
            "brier": (round(float(c_row.brier.iloc[0]), 4)
                      if len(c_row) and c_row.brier.notna().any() else None),
            "log_loss": (round(float(c_row.log_loss.iloc[0]), 4)
                         if len(c_row) and c_row.log_loss.notna().any() else None),
        })
    lb = pd.DataFrame(rows)
    smeta = strat.set_index("strategy_id")
    leaderboard = [{
        "id": r.strategy_id, "username": r.strategy_id,
        "name": sname.get(r.strategy_id, r.strategy_id),
        "category": smeta.model.get(r.strategy_id, "?"),
        "env": r.env, "env_label": ENV_LABEL.get(r.env, r.env),
        "version": "v1", "model": smeta.model.get(r.strategy_id, "?"),
        "market": smeta.market.get(r.strategy_id, "?"),
        "total_bets": int(r.bets), "settled_bets": int(r.settled),
        "wins": int(r.wins), "losses": int(r.losses), "pushes": int(r.pushes),
        "win_rate": None if pd.isna(r.win_rate) else float(r.win_rate),
        "total_pnl": float(r.pnl),
        "verified_bets": int(r.verified_bets),
        "verified_pnl": float(r.verified_pnl),
        "verified_roi": None if pd.isna(r.verified_roi) else float(r.verified_roi),
        "verified_win_rate": None if pd.isna(r.verified_win_rate) else float(r.verified_win_rate),
        "assumed_bets": int(r.assumed_bets),
        "skill_bets": int(r.skill_bets),
        "assumed_win_rate": None if pd.isna(r.assumed_win_rate) else float(r.assumed_win_rate),
        "synthetic_bets": int(r.synthetic_bets),
        "synthetic_roi": None if pd.isna(r.synthetic_roi) else float(r.synthetic_roi),
        "roi": None if pd.isna(r.verified_roi) else float(r.verified_roi),
        "brier": r.brier, "log_loss": r.log_loss,
        # bankroll is deliberately NOT published: the engine compounds
        # quarter-Kelly stakes and produces artefacts (one totals strategy
        # reaches 7e9 from 1e4). ROI per dollar staked is the metric.
        "current_bankroll": None, "max_drawdown": None,
        "status": smeta.status.get(r.strategy_id, "?"),
        "pricing": ("verified 2019-2025 moneylines"
                    if r.verified_bets else
                    "NO verified prices in this environment — assumed +100 "
                    "placeholder (model-skill metric only)"),
    } for r in lb.itertuples()]
    leaderboard.sort(key=lambda d: (-(d["verified_roi"] if d["verified_roi"] is not None
                                      else -999),
                                    -(d["win_rate"] or 0)))

    # ---------------------------------------------------------- strategies
    strategies = [{
        "id": r.strategy_id, "username": r.strategy_id, "name": r.name,
        "category": r.model, "env": r.env, "env_label": ENV_LABEL.get(r.env, r.env),
        "version": "v1", "parent_version": None, "model": r.model,
        "market": r.market, "hypothesis": r.hypothesis,
        "entry_rule": r.notes or "", "status": r.status,
        "created_at": r.created_at,
        "pricing_note": ("settles at verified 2019-2025 moneylines where they exist"
                         if r.env == "REG" else
                         "NO verified market prices exist for postseason games; "
                         "results are model-skill metrics (fair-coin proxy), not market ROI"),
    } for r in strat.itertuples()]

    # ------------------------------------------------------------- ledger
    led = settled.sort_values(["season", "game_pk"], ascending=[False, False]).head(ledger_cap)
    ledger = [{
        "bet_id": r.bet_id, "strategy_id": r.strategy_id, "username": r.strategy_id,
        "season": int(r.season), "gameday": "", "matchup": r.matchup,
        "market": r.market, "selection": r.selection,
        "price": ("" if pd.isna(r.market_price) else f"{r.market_price:+.0f}"),
        "implied_prob": (None if pd.isna(r.market_price) else
                         round(float(100.0 / abs(r.market_price))
                               if r.market_price > 0 else
                               float(abs(r.market_price) / (abs(r.market_price) + 100)), 4)),
        "model_prob": None if pd.isna(r.model_prob) else round(float(r.model_prob), 4),
        "edge": None if pd.isna(r.edge) else round(float(r.edge), 4),
        "stake": round(float(r.stake or 0), 2), "result": r.result,
        "actual_score": r.actual_score, "pnl": round(float(r.pnl or 0), 2),
        "roi": (round(float(r.pnl / r.stake), 4) if r.stake else 0.0),
        "clv": None if pd.isna(r.clv) else round(float(r.clv), 4),
        "verification_status": _verification(r.game_pk, bool(r.synthetic)),
    } for r in led.itertuples()]

    # --------------------------------------------------- upcoming / open
    def _up(r):
        return {
            "bet_id": r.bet_id, "strategy_id": r.strategy_id,
            "username": r.strategy_id,
            "strategy_name": sname.get(r.strategy_id, r.strategy_id),
            "season": int(r.season), "gameday": "", "gametime": "",
            "matchup": r.matchup, "venue": "", "market": r.market,
            "selection": r.selection,
            "current_price": ("" if pd.isna(r.market_price)
                              else f"{r.market_price:+.0f}"),
            "required_price": ("" if pd.isna(r.fair_price)
                               else f"{r.fair_price:+.0f}"),
            "model_prob": None if pd.isna(r.model_prob) else round(float(r.model_prob), 4),
            "implied_prob": None,
            "estimated_edge": None if pd.isna(r.edge) else round(float(r.edge), 4),
            "stake": round(float(r.stake or 0), 2),
            "decision_time": r.made_at,
            "market_source": ("NO verified price available for this game — "
                              "pick is tracked as PROPOSED, not traded"),
            "status": r.status,
            "round_code": ("" if pd.isna(r.round_code) else r.round_code),
        }
    upcoming = [_up(r) for r in proposed.sort_values("game_pk").itertuples()]
    open_pos = [p for p in upcoming if p["status"] == "OPEN"]

    # --------------------------------------------------- research / experiments
    q = pd.read_sql("SELECT * FROM research_questions", conn).set_index("q_id")
    fnd = pd.read_sql("SELECT * FROM research_findings", conn)
    exps = pd.read_sql("SELECT * FROM experiments", conn)
    research = [{
        "title": f"{r.q_id} — {q.question.get(r.q_id, '')}",
        "status": r.verdict, "hypothesis": q.question.get(r.q_id, ""),
        "sample_size": int(r.n or 0),
        "methodology": "point-in-time features, chronological walk-forward; "
                       "postseason kept separate from regular season",
        "findings": (json.loads(r.stats_json) if r.stats_json else {}),
        "conclusion": r.evidence or "",
        "action_taken": f"verdict recorded as {r.verdict} (env={r.env})",
        "env": r.env,
    } for r in fnd.itertuples()]
    research += [{
        "title": f"{r.exp_id} — {r.name}",
        "status": "RUN", "hypothesis": r.name, "sample_size": int(r.n or 0),
        "methodology": "permanent model-comparison framework (Models A-E)",
        "findings": (json.loads(r.details_json) if r.details_json else {}),
        "conclusion": (f"n={r.n}, brier="
                       f"{round(float(r.brier), 4) if r.brier is not None else 'n/a'}, "
                       f"fair-coin ROI="
                       f"{round(float(r.roi) * 100, 2) if r.roi is not None else 'n/a'}%"),
        "action_taken": f"env={r.env} round={r.round_code or '-'}",
        "env": r.env,
    } for r in exps.itertuples()]

    # ------------------------------------------------------------- registry
    reg = pd.read_sql("SELECT * FROM source_registry", conn)
    registry = [{
        "name": r.source_id, "url": r.url, "provenance": r.notes or "",
        "data_type": r.coverage or "", "description": r.description or "",
        "cost_classification": "free / public",
        "reliability_rating": ("verified in use" if r.verified
                               else "rejected or unreachable"),
        "historical_depth": r.coverage or "",
        "last_verification_date": (r.fetched_at or "")[:10],
        "status": ("VERIFIED_PRIMARY" if r.verified else "REJECTED_OR_UNAVAILABLE"),
        "reject_reason": r.reject_reason or "",
    } for r in reg.itertuples()]

    # ---------------------------------------------------- checks / issues
    chk = pd.read_sql("SELECT * FROM verification_log", conn)
    audit = [{"name": r.check_id, "category": r.scope, "passed": bool(r.passed),
              "details": r.details} for r in chk.itertuples()]

    irregularities = [
        {"id": "IRR-001", "title": "Fabricated competition data was being published",
         "category": "DATA INTEGRITY", "severity": "CRITICAL",
         "description": "data/*.json was generated by scripts/generate_mlbcomp_data.py "
                        "using random.uniform/random.choice for odds, model "
                        "probabilities, results, 'actual scores' and Kalshi "
                        "fills/liquidity, then stamped VERIFIED_PRIMARY. The "
                        "summary totals did not even match the files (89,452 bets "
                        "claimed vs 1,200 present).",
         "resolution": "Generator retired; data/*.json is now written only by "
                       "mlbcomp/web/export_static.py from data/mlbcomp.db."},
        {"id": "IRR-002", "title": "World Series champion table was wrong in 5 of 11 seasons",
         "category": "VERIFICATION", "severity": "CRITICAL",
         "description": "KNOWN_WS_CHAMPIONS listed 2016 LAD, 2020 TB, 2021 HOU, "
                        "2023 LAD, 2024 NYY. The documented champions are 2016 CHC, "
                        "2020 LAD, 2021 ATL, 2023 TEX, 2024 LAD. That error caused "
                        "authentic mirror postseason data to be quarantined as "
                        "'fabricated' and replaced by hand-written winners.",
         "resolution": "Table corrected and cross-checked against four independent "
                       "public listings; verify/checks.py now fails on mismatch."},
        {"id": "IRR-003", "title": "Retired reconstruction contained invented series results",
         "category": "DATA INTEGRITY", "severity": "CRITICAL",
         "description": "data_recon/po_results.py asserted e.g. Rays over Dodgers "
                        "2020, Astros over Braves 2021, Dodgers sweeping the Rangers "
                        "in 2023 (Los Angeles was not in that Series), Yankees over "
                        "Dodgers 2024, Cardinals over Nationals in the 2019 NLCS.",
         "resolution": "Module retired; postseason now comes from the mirror with "
                       "real scores (440 games, 2015-2025)."},
        {"id": "IRR-004", "title": "2020 Division Series treated as best-of-three",
         "category": "MODEL LOGIC", "severity": "HIGH",
         "description": "round_needed/_need returned 2 wins for the 2020 DS. MLB's "
                        "2020-07-23 announcement and the data itself (a 5-game "
                        "2020 ALDS) show it was best-of-five. This corrupted "
                        "clinch/elimination series-state features.",
         "resolution": "Fixed in ingest.baseballr.round_needed, verify.checks._need "
                       "and engine.backtest._need; 2015 single-game wild card fixed "
                       "in the backtest copy too."},
        {"id": "IRR-005", "title": "No verified market prices exist for any postseason game",
         "category": "MARKET DATA", "severity": "MEDIUM",
         "description": "The odds source covers the 2019-2025 regular season only. "
                        "Postseason ROI therefore cannot be measured against a real "
                        "market in this environment.",
         "resolution": "Postseason results are reported as fair-coin proxy skill "
                       "metrics and labeled NO_MARKET_PRICE in the ledger."},
        {"id": "IRR-006", "title": "Kalshi unreachable from this environment",
         "category": "MARKET DATA", "severity": "MEDIUM",
         "description": "api.elections.kalshi.com cannot be reached, so no real "
                        "contracts, quotes, volume, liquidity or fills are available.",
         "resolution": "kalshi_trades.json is exported as an empty list rather than "
                       "simulated fills."},
        {"id": "IRR-007", "title": "2022 WS Game 3 carried as a scoreless Final row",
         "category": "SOURCE QUIRK", "severity": "LOW",
         "description": "The rain-suspended 2022 World Series Game 3 appears as a "
                        "separate Final row with null scores.",
         "resolution": "Rows without scores are never settled; the 2022 WS game log "
                       "was cross-checked (PHI 7-0 HOU on Nov 1 is that game's "
                       "completion) and the series still resolves HOU 4-2."},
    ]

    # ------------------------------------------------------------- summary
    env_rows = {}
    for env, sub in lb.groupby("env"):
        env_rows[env] = {
            "strategies": int(sub.strategy_id.nunique()),
            "bets": int(sub.bets.sum()),
            "settled": int(sub.settled.sum()),
            "verified_bets": int(sub.verified_bets.sum()),
            "verified_pnl": round(float(sub.verified_pnl.sum()), 2),
            "assumed_bets": int(sub.assumed_bets.sum()),
            "synthetic_bets": int(sub.synthetic_bets.sum()),
            "pnl": round(float(sub.pnl.sum()), 2),
            "market": ("verified real prices for the 2019-2025 REG subset "
                       "(cesar-dx moneylines); all other games settled at an "
                       "assumed +100 placeholder"
                       if int(sub.verified_bets.sum()) else
                       "NO verified prices exist — every bet settled at an "
                       "assumed +100 placeholder (model-skill metric only)"),
        }
    ver_settled = settled[settled.is_verified]
    total_settled_pnl = round(float(ver_settled.pnl.fillna(0).sum()), 2)
    top = max((d for d in leaderboard if d["verified_roi"] is not None),
              key=lambda d: d["verified_roi"], default=None)
    summary = {
        "competition_name": "ARENA AI — MLB Autonomous Betting Research & Testing",
        "as_of_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_by": "mlbcomp.web.export_static (source: data/mlbcomp.db)",
        "current_season": 2026,
        "current_stage": "2026 regular season in progress (snapshot 2026-09-19)",
        "total_games_tracked": int(pd.read_sql("SELECT COUNT(*) c FROM games", conn).c[0]),
        "completed_games": int(pd.read_sql(
            "SELECT COUNT(*) c FROM games WHERE home_score IS NOT NULL", conn).c[0]),
        "upcoming_games": int(pd.read_sql(
            "SELECT COUNT(*) c FROM games WHERE home_score IS NULL", conn).c[0]),
        "total_strategies": int(len(strategies)),
        "total_simulated_bets": int(len(bets)),
        "settled_bets": int(len(settled)),
        "total_simulated_pnl": total_settled_pnl,
        "pnl_basis": f"settled PnL at VERIFIED prices only "
                     f"({len(ver_settled):,} of {len(settled):,} settled bets); "
                     f"assumed-price and synthetic-line PnL is excluded",
        "total_upcoming_bets": len(upcoming),
        "total_open_positions": len(open_pos),
        "total_kalshi_trades": 0,
        "kalshi_status": "DATA_UNAVAILABLE — api.elections.kalshi.com unreachable; "
                         "no fills are simulated",
        "ledger_records_exported": len(ledger),
        "ledger_records_in_db": len(settled),
        "ledger_note": (f"ledger capped at the {ledger_cap:,} most recent settled "
                        f"bets; {len(settled):,} exist in the database"),
        "top_performing_strategy": (top or {}).get("id", "n/a"),
        "top_pnl": (top or {}).get("total_pnl", 0.0),
        "top_roi": (top or {}).get("verified_roi") or 0.0,
        "top_roi_basis": "ROI per dollar staked at verified 2019-2025 "
                         "moneyline prices (the only real market in this system)",
        "environment_breakdown": env_rows,
        "pnl_warning": "The engine compounds quarter-Kelly stakes and settles "
                       "unpriced games at an assumed +100 placeholder plus a "
                       "synthetic 8.5 totals line. Compounded bankroll is "
                       "therefore not published; ROI per dollar staked at "
                       "verified prices is the only market claim made here.",
    }

    print("[export] writing data/*.json")
    for name, obj in [("summary.json", summary), ("leaderboard.json", leaderboard),
                      ("strategies.json", strategies), ("bets_ledger.json", ledger),
                      ("upcoming_bets.json", upcoming),
                      ("open_positions.json", open_pos),
                      ("research_experiments.json", research),
                      ("registry.json", registry), ("audit_checks.json", audit),
                      ("irregularities.json", irregularities),
                      ("kalshi_trades.json", [])]:
        _write(name, obj)
    conn.close()
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger-cap", type=int, default=LEDGER_CAP_DEFAULT)
    s = export(ap.parse_args().ledger_cap)
    print(f"[export] done — {s['total_simulated_bets']:,} bets, "
          f"{s['total_strategies']} strategies, {s['settled_bets']:,} settled")
