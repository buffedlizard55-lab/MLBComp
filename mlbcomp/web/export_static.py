"""Build the GitHub Pages payload from the database, never from random data.

When the checkout has no fetched source snapshot, export still succeeds with a
truthful ``NO_SOURCE_SNAPSHOT`` dashboard: strategy hypotheses and controls are
visible, while games, prices, bets, fills, results and PnL remain empty.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .. import db
from ..config import DATA, FEAT, ENVS, ENV_LABEL, ROUNDS, ROUND_LABEL, STARTING_BANKROLL, TODAY
from ..engine.catalog import register_catalog
from ..engine.competition import DEFAULT_SEED, ENGINE_VERSION as COMPETITION_VERSION, build_payloads
from ..engine.strategies import build_catalog
from ..sources import registry_snapshot

LEDGER_CAP_DEFAULT = 20_000


def _clean(value: Any):
    if isinstance(value, float) and (not math.isfinite(value)):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if hasattr(value, "item"):
        try:
            return _clean(value.item())
        except (ValueError, TypeError):
            pass
    return value


def _write(name: str, value: Any) -> None:
    (DATA / name).write_text(json.dumps(_clean(value), indent=2, allow_nan=False, default=str) + "\n")


def _db_frame(sql: str, params=()) -> pd.DataFrame:
    try:
        return db.query_df(sql, params)
    except Exception:
        return pd.DataFrame()


def _strategy_records() -> list[dict[str, Any]]:
    catalog = register_catalog(build_catalog())
    return [s.to_record() for s in catalog]


def _metrics() -> pd.DataFrame:
    bets = _db_frame("SELECT * FROM bets")
    if bets.empty:
        return pd.DataFrame()
    rows = []
    for (sid, env), group in bets.groupby(["strategy_id", "env"]):
        scored = group[group.result.isin(["W", "L", "P"])]
        verified = scored[scored.verification_status == "VERIFIED_PRICE"]
        stake = float(verified.stake.fillna(0).sum()) if len(verified) else None
        pnl = float(verified.pnl.fillna(0).sum()) if len(verified) else None
        decisions = int((scored.result.isin(["W", "L"])).sum())
        wins = int((scored.result == "W").sum())
        rows.append({"strategy_id": sid, "env": env, "total_bets": len(group),
                     "settled_bets": int((group.status == "SETTLED").sum()),
                     "eval_picks": int((group.status == "EVAL").sum()),
                     "wins": wins, "losses": int((scored.result == "L").sum()),
                     "pushes": int((scored.result == "P").sum()),
                     "win_rate": wins / decisions if decisions else None,
                     "total_pnl": pnl, "verified_bets": len(verified),
                     "verified_stake": stake, "verified_roi": pnl / stake if stake else None,
                     "metric_status": "EVALUATED" if len(scored) else "NO_DATA"})
    return pd.DataFrame(rows)


def _leaderboard(strategies: list[dict], metrics: pd.DataFrame) -> list[dict]:
    metric_map = {(r.strategy_id, r.env): r for r in metrics.itertuples()} if not metrics.empty else {}
    cal = _db_frame("SELECT strategy_id, env, brier, log_loss, n_games FROM calibration")
    cal_map = {(r.strategy_id, r.env): r for r in cal.itertuples()} if not cal.empty else {}
    rows = []
    for strategy in strategies:
        sid, env = strategy["sid"], strategy["env"]
        m = metric_map.get((sid, env))
        c = cal_map.get((sid, env))
        has_verified = m is not None and int(m.verified_bets) > 0
        total_pnl = float(m.total_pnl) if has_verified and m.total_pnl is not None else None
        roi = float(m.verified_roi * 100) if has_verified and m.verified_roi is not None else None
        row = {
            "id": sid, "username": sid, "name": strategy["name"],
            "category": strategy["model"], "env": env, "env_label": ENV_LABEL.get(env, env),
            "version": strategy.get("version", "v1"), "model": strategy["model"],
            "market": strategy["market"], "total_bets": int(m.total_bets) if m is not None else 0,
            "settled_bets": int(m.settled_bets) if m is not None else 0,
            "eval_picks": int(m.eval_picks) if m is not None else 0,
            "wins": int(m.wins) if m is not None else 0,
            "losses": int(m.losses) if m is not None else 0,
            "pushes": int(m.pushes) if m is not None else 0,
            "win_rate": float(m.win_rate * 100) if m is not None and m.win_rate is not None else None,
            "total_pnl": round(total_pnl, 2) if total_pnl is not None else None,
            "verified_bets": int(m.verified_bets) if m is not None else 0,
            "verified_stake": round(float(m.verified_stake), 2) if has_verified else None,
            "verified_pnl": round(total_pnl, 2) if total_pnl is not None else None,
            "verified_roi": roi,
            "roi": roi,
            "initial_bankroll": STARTING_BANKROLL,
            "current_bankroll": round(STARTING_BANKROLL + total_pnl, 2) if total_pnl is not None else None,
            "max_drawdown": None,
            "equity_curve": ([STARTING_BANKROLL, round(STARTING_BANKROLL + total_pnl, 2)]
                             if total_pnl is not None else []),
            "brier": (float(c.brier) if c is not None and c.brier is not None
                      and pd.notna(c.brier) else None),
            "log_loss": (float(c.log_loss) if c is not None and c.log_loss is not None
                         and pd.notna(c.log_loss) else None),
            "calibration_n": (int(c.n_games) if c is not None and pd.notna(c.n_games)
                              else 0),
            "status": strategy.get("status", "NOT_RUN"),
            "metric_status": m.metric_status if m is not None else "NO_DATA",
            "experiment_alias": _is_experiment_alias(sid),
            "alias_of": (strategy.get("extra") or {}).get("alias_of"),
            "pricing_note": ("No verified quote means EVAL/PROPOSED only; PnL and ROI "
                             "remain null. Brier/log loss score model calibration on "
                             "settled outcomes regardless of price."),
        }
        rows.append(row)
    return rows


def _chain_status() -> bool | None:
    try:
        from ..engine.ledger import verify_chain
        chain = verify_chain()
        return bool(chain["valid"])
    except Exception:
        return None


def _ledger(cap: int) -> list[dict[str, Any]]:
    # Verified-price rows first: a capped export would otherwise contain zero
    # reviewable wagers and no usable closing/quote provenance (observed on the
    # 2026-09-22 snapshot export, which held 20,000 NO_MARKET_PRICE rows).
    bets = _db_frame(
        "SELECT * FROM bets ORDER BY "
        "CASE WHEN verification_status = 'VERIFIED_PRICE' THEN 0 ELSE 1 END, bet_id DESC")
    if bets.empty:
        return []
    games = _db_frame("SELECT game_pk, home_team_id, away_team_id, game_date FROM games")
    teams = _db_frame("SELECT team_id, abbr, name FROM teams")
    strategies = _db_frame("SELECT strategy_id, model FROM strategies")
    id2abbr = {int(r.team_id): r.abbr for r in teams.itertuples()} if not teams.empty else {}
    game_map = {int(r.game_pk): r for r in games.itertuples()} if not games.empty else {}
    model_map = {r.strategy_id: r.model for r in strategies.itertuples()} if not strategies.empty else {}
    out = []
    for r in bets.head(cap).itertuples():
        row = {k: getattr(r, k) for k in bets.columns}
        game = game_map.get(int(r.game_pk))
        row["home_team_id"] = getattr(game, "home_team_id", None) if game is not None else None
        row["away_team_id"] = getattr(game, "away_team_id", None) if game is not None else None
        row["home_abbr"] = id2abbr.get(int(getattr(game, "home_team_id", 0) or 0)) if game is not None else None
        row["away_abbr"] = id2abbr.get(int(getattr(game, "away_team_id", 0) or 0)) if game is not None else None
        row["game_date"] = getattr(game, "game_date", None) if game is not None else None
        row["model"] = model_map.get(r.strategy_id)
        row["hash_chained"] = (r.verification_status == "VERIFIED_PRICE")
        out.append(row)
    return out


def _upcoming() -> list[dict[str, Any]]:
    predictions = _db_frame("SELECT * FROM predictions ORDER BY decision_time, prediction_id")
    if predictions.empty:
        return []
    # Only future/incomplete games are \"upcoming\"; historical predictions are
    # evaluation records, not forward proposals.
    games = _db_frame("SELECT game_pk,game_date,start_utc,home_team_id,away_team_id,round_code,home_score FROM games")
    if games.empty:
        # No games table -> fall back to empty upcoming rather than leaking history
        return []
    # True upcoming = scheduled future (game_date >= TODAY), not historic cancelled/incomplete voids
    upcoming_candidates = games[games.home_score.isna()]
    if not upcoming_candidates.empty:
        upcoming_candidates = upcoming_candidates[upcoming_candidates.game_date.astype(str) >= TODAY]
    upcoming_pks = set(upcoming_candidates.game_pk.astype(int).tolist()) if not upcoming_candidates.empty else set()
    # Fallback: if DB filter yields nothing but parquet has scheduled future, use parquet
    if not upcoming_pks and FEAT.exists():
        try:
            import pandas as pd
            if (FEAT / "games.parquet").exists():
                g = pd.read_parquet(FEAT / "games.parquet")
                g_up = g[g.home_score.isna() & (g.game_date.astype(str) >= TODAY)]
                upcoming_pks = set(g_up.game_pk.astype(int).tolist())
        except Exception:
            pass
    game_map = {int(r.game_pk): r for r in games.itertuples()} if not games.empty else {}
    filtered = predictions[predictions.game_pk.astype(int).isin(upcoming_pks)] if upcoming_pks else predictions.iloc[0:0]
    # Reasoning inputs for the Postseason Center and forward-test views
    # (spec §17): series state, probable pitchers when source-backed, price
    # fields and the decision timestamp — missing inputs stay null/flagged.
    try:
        state = _db_frame("SELECT * FROM series_state")
        state_map = {int(r.game_pk): r for r in state.itertuples()} if not state.empty else {}
    except Exception:
        state_map = {}
    try:
        pitchers = _db_frame("SELECT * FROM starting_pitchers")
        sp_map: dict[int, dict] = {}
        if not pitchers.empty:
            for r in pitchers.itertuples():
                sp_map.setdefault(int(r.game_pk), {})[
                    "home" if r.team_id is not None else "away"] = r.player_id
    except Exception:
        sp_map = {}
    out_rows = []
    for r in filtered.itertuples():
        st = state_map.get(int(r.game_pk))
        game = game_map.get(int(r.game_pk))
        out_rows.append({
            "prediction_id": r.prediction_id, "strategy_version_id": r.strategy_version_id,
            "game_pk": int(r.game_pk), "round_code": r.round_code,
            "decision_time": r.decision_time, "selection": r.selection,
            "model_probability": r.model_probability, "fair_price": r.fair_price,
            "required_price": r.required_price, "edge": r.edge,
            "status": "PROPOSED", "market_price": None,
            "verification_status": "NO_MARKET_PRICE",
            "game_available": int(r.game_pk) in game_map,
            "environment": r.environment,
            "series_state": ({
                "series_key": getattr(st, "series_key", None),
                "game_number": getattr(st, "game_number", None),
                "wins_a_before": getattr(st, "wins_a_before", None),
                "wins_b_before": getattr(st, "wins_b_before", None),
                "elimination_a": getattr(st, "elimination_a", None),
                "elimination_b": getattr(st, "elimination_b", None),
                "clinch_a": getattr(st, "clinch_a", None),
                "clinch_b": getattr(st, "clinch_b", None),
                "games_remaining": getattr(st, "games_remaining", None),
                "days_rest_home": getattr(st, "days_rest_home", None),
                "days_rest_away": getattr(st, "days_rest_away", None),
            } if st is not None else None),
            "probable_pitchers": (sp_map.get(int(r.game_pk)) or None),
            "probable_pitchers_status": (
                "OBSERVED" if int(r.game_pk) in sp_map else "DATA_UNAVAILABLE"),
            "home_team_id": getattr(game, "home_team_id", None) if game is not None else None,
            "away_team_id": getattr(game, "away_team_id", None) if game is not None else None,
            "game_date": getattr(game, "game_date", None) if game is not None else None,
            "reasoning_inputs": [
                "series state (pre-game)",
                "point-in-time team features",
                "probable pitchers" + ("" if int(r.game_pk) in sp_map else " — DATA_UNAVAILABLE"),
                "observed price" + " — DATA_UNAVAILABLE (no verified quote at decision time)",
                "required price / model probability / fair price / edge shown above",
                "decision timestamp shown above",
            ],
        })
    return out_rows


def _question_titles() -> dict[str, str]:
    from ..config import RESEARCH_QUESTIONS
    return dict(RESEARCH_QUESTIONS)


def _research() -> list[dict[str, Any]]:
    findings = _db_frame("SELECT * FROM research_findings ORDER BY q_id")
    experiments = _db_frame("SELECT * FROM experiments ORDER BY exp_id")
    titles = _question_titles()
    output = []
    if not findings.empty:
        for r in findings.itertuples():
            try:
                values = json.loads(r.stats_json) if r.stats_json else {}
            except (TypeError, json.JSONDecodeError):
                values = {}
            output.append({"id": r.q_id, "title": titles.get(r.q_id, r.q_id), "status": r.verdict,
                           "sample_size": int(r.n or 0), "findings": values,
                           "conclusion": r.evidence, "env": r.env,
                           "round_code": r.round_code, "provenance": r.provenance})
    if not experiments.empty:
        for r in experiments.itertuples():
            try:
                details = json.loads(r.details_json) if r.details_json else {}
            except (TypeError, json.JSONDecodeError):
                details = {}
            output.append({"id": r.exp_id, "title": r.name, "status": r.status,
                           "sample_size": int(r.n or 0), "findings": details,
                           "brier": r.brier, "log_loss": r.logloss, "roi": r.roi,
                           "env": r.env, "round_code": r.round_code,
                           "conclusion": "Comparison is not a superiority claim; preserve all models.",
                           "provenance": "experiments table"})
    return output


def _audit() -> list[dict[str, Any]]:
    checks = _db_frame("SELECT check_id,scope,passed,details FROM verification_log ORDER BY check_id")
    return [{"name": r.check_id, "category": r.scope, "passed": bool(r.passed), "details": r.details}
            for r in checks.itertuples()] if not checks.empty else []


def _issues() -> list[dict[str, Any]]:
    issues = _db_frame("SELECT * FROM data_issues ORDER BY detected_at DESC")
    if issues.empty:
        snapshot = (FEAT / "games.parquet").exists()
        return [{
            "id": "ISSUE-QUEUE-EMPTY",
            "title": "No issues recorded",
            "severity": "INFO",
            "status": "OPEN",
            "description": ("The queue is ready; no source snapshot has been loaded."
                            if not snapshot else
                            "The queue is ready; the loaded source snapshot has not "
                            "produced any open data issues."),
            "resolution": None,
        }]
    return [{"id": r.issue_id, "title": r.issue_type, "severity": r.severity,
             "status": r.status, "description": r.description, "resolution": r.resolved_at}
            for r in issues.itertuples()]


EXPERIMENT_ALIAS_PREFIX = "MLB_POST_MODEL_"


def _is_experiment_alias(strategy_id: str | None) -> bool:
    return str(strategy_id or "").startswith(EXPERIMENT_ALIAS_PREFIX)


def export(ledger_cap: int = LEDGER_CAP_DEFAULT, force: bool = False) -> dict[str, Any]:
    """Project the database (or the safe empty catalog) to data/*.json.

    Refuses to overwrite a committed SOURCE_SNAPSHOT when games.parquet is
    missing, unless ``force`` is set.  That prevents a catalog-only rebuild
    from silently erasing verified competition numbers.
    """
    db.init_db()
    source_snapshot = bool((FEAT / "games.parquet").exists())
    existing_summary = DATA / "summary.json"
    if existing_summary.exists() and not source_snapshot and not force:
        try:
            previous = json.loads(existing_summary.read_text())
        except (OSError, json.JSONDecodeError):
            previous = {}
        if previous.get("data_mode") == "SOURCE_SNAPSHOT":
            raise RuntimeError(
                "Refusing to overwrite a SOURCE_SNAPSHOT export because "
                "data/features/games.parquet is missing. Re-run ingest/backtest "
                "or pass --force to emit NO_SOURCE_SNAPSHOT."
            )
    strategies = _strategy_records()
    metrics = _metrics()
    if not metrics.empty:
        metric_keys = {(r.strategy_id, r.env) for r in metrics.itertuples() if int(r.total_bets) > 0}
        for strategy in strategies:
            if (strategy["sid"], strategy["env"]) in metric_keys and strategy.get("status") == "NOT_RUN":
                strategy["status"] = "BACKTESTED"
    leaderboard = _leaderboard(strategies, metrics)
    ledger_rows = _ledger(ledger_cap)
    games = _db_frame("SELECT * FROM games")
    bets = _db_frame("SELECT * FROM bets")
    verified_bets = bets[bets.verification_status == "VERIFIED_PRICE"] if not bets.empty else bets
    real_pnl = float(verified_bets.pnl.fillna(0).sum()) if not verified_bets.empty else None
    if games.empty:
        completed_games = upcoming_games = incomplete_historical = 0
    else:
        completed_games = int(games.home_score.notna().sum())
        no_score = games.home_score.isna()
        future = games.game_date.astype(str) >= TODAY
        upcoming_games = int((no_score & future).sum())
        incomplete_historical = int((no_score & ~future).sum())
    env_breakdown = {}
    unique_env_breakdown = {}
    alias_ids = {s["sid"] for s in strategies if _is_experiment_alias(s.get("sid")) or s.get("extra", {}).get("alias_of")}
    for env in ["REG", "POST", *ROUNDS]:
        subset = metrics[metrics.env == env] if not metrics.empty else pd.DataFrame()
        unique = subset[~subset.strategy_id.isin(alias_ids)] if not subset.empty else subset
        env_breakdown[env] = {
            "strategies": int(sum(s["env"] == env for s in strategies)),
            "records": int(subset.total_bets.sum()) if not subset.empty else 0,
            "settled": int(subset.settled_bets.sum()) if not subset.empty else 0,
            "evaluation_picks": int(subset.eval_picks.sum()) if not subset.empty else 0,
            "verified_bets": int(subset.verified_bets.sum()) if not subset.empty else 0,
            "verified_pnl": (round(float(subset.total_pnl.sum()), 2)
                             if not subset.empty and subset.verified_bets.sum() > 0 else None),
            "status": ("NO_DATA" if subset.empty else
                       "NO_VERIFIED_MARKET_PRICE" if subset.verified_bets.sum() == 0 else "EVALUATED"),
        }
        unique_env_breakdown[env] = {
            "strategies": int(sum(s["env"] == env and s["sid"] not in alias_ids for s in strategies)),
            "records": int(unique.total_bets.sum()) if not unique.empty else 0,
            "settled": int(unique.settled_bets.sum()) if not unique.empty else 0,
            "evaluation_picks": int(unique.eval_picks.sum()) if not unique.empty else 0,
            "verified_bets": int(unique.verified_bets.sum()) if not unique.empty else 0,
            "verified_pnl": (round(float(unique.total_pnl.sum()), 2)
                             if not unique.empty and unique.verified_bets.sum() > 0 else None),
            "status": ("NO_DATA" if unique.empty else
                       "NO_VERIFIED_MARKET_PRICE" if unique.verified_bets.sum() == 0 else "EVALUATED"),
        }
    unique_verified = (metrics[~metrics.strategy_id.isin(alias_ids)]
                       if not metrics.empty else metrics)
    unique_pnl = None
    if not unique_verified.empty:
        priced = unique_verified[unique_verified.verified_bets.fillna(0) > 0]
        if not priced.empty and priced.total_pnl.notna().any():
            unique_pnl = round(float(priced.total_pnl.fillna(0).sum()), 2)
    # A fetch manifest alone is not a usable model snapshot; normalized games
    # must pass ingest before the site leaves its safe empty mode.
    source_snapshot = bool((FEAT / "games.parquet").exists())
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    comp_seed = os.environ.get("MLBCOMP_COMP_SEED", DEFAULT_SEED)
    competitors_payload, competitions_payload = build_payloads(
        strategies, leaderboard, ledger_rows,
        seed=comp_seed, generated_at=generated_at)
    summary = {
        "competition_name": "ARENA AI — MLB Autonomous MLB Research & Paper Competition",
        "as_of_date": TODAY,
        "generated_at": generated_at,
        "generated_by": "mlbcomp.web.export_static",
        "data_mode": "SOURCE_SNAPSHOT" if source_snapshot else "NO_SOURCE_SNAPSHOT",
        "current_season": 2026,
        "current_stage": "No source snapshot loaded — no upcoming wager is asserted" if not source_snapshot else "Source snapshot loaded; live availability requires a fresh run",
        "total_games_tracked": int(len(games)), "completed_games": completed_games,
        "upcoming_games": upcoming_games,
        "incomplete_historical_games": incomplete_historical,
        "total_strategies": len(strategies), "total_simulated_bets": len(bets),
        "total_competition_entrants": len(competitors_payload["entrants"]),
        "total_competitions": len(competitions_payload["competitions"]),
        "competition_seed": comp_seed,
        "competition_generator": COMPETITION_VERSION,
        "competitions_note": (
            "Simulated competitions: randomized window start dates (recorded seed) over the "
            "exported records; entrants are personas bound to transparent strategy slices; "
            "standings are deterministic re-aggregations of real records; PnL is published "
            "only where VERIFIED_PRICE rows exist in the window."
        ),
        "settled_bets": int((bets.status == "SETTLED").sum()) if not bets.empty else 0,
        "total_simulated_pnl": round(real_pnl, 2) if real_pnl is not None else None,
        "unique_verified_pnl": unique_pnl,
        "pnl_basis": "Verified observed quotes only; no price means no stake, PnL or ROI.",
        "alias_note": ("MLB_POST_MODEL_A–E are experiment aliases of catalog strategies. "
                       "environment_breakdown includes them; unique_environment_breakdown and "
                       "unique_verified_pnl exclude them so POST PnL is not double-counted."),
        "total_upcoming_bets": len(_upcoming()), "total_open_positions": int(_db_frame("SELECT * FROM positions WHERE state='OPEN'").shape[0]),
        "total_kalshi_trades": int(_db_frame("SELECT * FROM immutable_ledger WHERE market='PREDICTION_MARKET'").shape[0]),
        "kalshi_status": "OPTIONAL — no contracts, quotes, fills or liquidity are created without verified API observations",
        "ledger_records_exported": len(ledger_rows),
        "bet_records_in_db": len(bets),
        "immutable_ledger_rows": int(_db_frame("SELECT * FROM immutable_ledger").shape[0]),
        "hash_chain_valid": (_chain_status()),
        "ledger_note": ("bet_records are EVAL/PROPOSED/OPEN/SETTLED predictions from the backtest; only "
                        "VERIFIED_PRICE rows are mirrored into the SHA-256 hash-chained immutable_ledger. "
                        "The static export may be capped for page size."),
        "top_performing_strategy": None, "top_pnl": None, "top_roi": None,
        "environment_breakdown": env_breakdown,
        "unique_environment_breakdown": unique_env_breakdown,
        "limitations": [
            "Source snapshot via api.github.com blobs: sportsdataverse/baseballr-data (schedule+play-by-play) + cesar-dx/mlb-betting-ml (moneyline) — content-addressed and cross-checked." if source_snapshot else "This checkout contains no fetched source snapshot unless data/raw or data/features is populated.",
            "Historical odds without an observed decision-time timestamp are not eligible for paper PnL; evaluation uses scores for Brier/log-loss only.",
            "Postseason has distinct REG/POST/WC/DS/LCS/WS models and leaderboards; a small sample does not establish an edge.",
            "No real-money order connector exists; all positions are paper-only.",
        ],
    }
    _write("summary.json", summary)
    _write("leaderboard.json", leaderboard)
    _write("strategies.json", strategies)
    _write("bets_ledger.json", ledger_rows)
    _write("competitors.json", competitors_payload)
    _write("competitions.json", competitions_payload)
    _write("upcoming_bets.json", _upcoming())
    _write("open_positions.json", [dict(r) for r in _db_frame("SELECT * FROM positions WHERE state='OPEN'").to_dict(orient="records")])
    _write("research_experiments.json", _research())
    _write("registry.json", registry_snapshot())
    _write("audit_checks.json", _audit())
    kalshi = _db_frame("SELECT * FROM immutable_ledger WHERE market='PREDICTION_MARKET' ORDER BY ledger_id DESC")
    kalshi_rows = kalshi.to_dict(orient="records") if not kalshi.empty else []
    _write("irregularities.json", _issues())
    _write("kalshi_trades.json", kalshi_rows)
    players = _db_frame("SELECT player_id, name FROM players WHERE name IS NOT NULL ORDER BY name")
    _write("players.json", [dict(r) for r in players.to_dict(orient="records")]) if not players.empty else _write("players.json", [])
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger-cap", type=int, default=LEDGER_CAP_DEFAULT)
    parser.add_argument("--force", action="store_true",
                        help="Allow overwriting a SOURCE_SNAPSHOT export with NO_SOURCE_SNAPSHOT")
    args = parser.parse_args()
    result = export(args.ledger_cap, force=args.force)
    print(json.dumps({"data_mode": result["data_mode"], "strategies": result["total_strategies"],
                      "bets": result["total_simulated_bets"]}, indent=2))
