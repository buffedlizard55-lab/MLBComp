"""Build the GitHub Pages payload from the database, never from random data.

When the checkout has no fetched source snapshot, export still succeeds with a
truthful ``NO_SOURCE_SNAPSHOT`` dashboard: strategy hypotheses and controls are
visible, while games, prices, bets, fills, results and PnL remain empty.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .. import db
from ..config import DATA, FEAT, ENVS, ENV_LABEL, ROUNDS, ROUND_LABEL, STARTING_BANKROLL, TODAY
from ..engine.catalog import register_catalog
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
    rows = []
    for strategy in strategies:
        sid, env = strategy["sid"], strategy["env"]
        m = metric_map.get((sid, env))
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
            "brier": None, "log_loss": None,
            "status": strategy.get("status", "NOT_RUN"),
            "metric_status": m.metric_status if m is not None else "NO_DATA",
            "pricing_note": "No verified quote means EVAL/PROPOSED only; PnL and ROI remain null.",
        }
        rows.append(row)
    return rows


def _ledger(cap: int) -> list[dict[str, Any]]:
    bets = _db_frame("SELECT * FROM bets ORDER BY bet_id DESC")
    if bets.empty:
        return []
    out = []
    for r in bets.head(cap).itertuples():
        out.append({k: getattr(r, k) for k in bets.columns})
    return out


def _upcoming() -> list[dict[str, Any]]:
    predictions = _db_frame("SELECT * FROM predictions ORDER BY decision_time, prediction_id")
    if predictions.empty:
        return []
    # Predictions are not automatically made bets.  Preserve the distinction.
    games = _db_frame("SELECT game_pk,game_date,start_utc,home_team_id,away_team_id,round_code FROM games")
    game_map = {int(r.game_pk): r for r in games.itertuples()} if not games.empty else {}
    return [{"prediction_id": r.prediction_id, "strategy_version_id": r.strategy_version_id,
             "game_pk": int(r.game_pk), "round_code": r.round_code,
             "decision_time": r.decision_time, "selection": r.selection,
             "model_probability": r.model_probability, "fair_price": r.fair_price,
             "required_price": r.required_price, "edge": r.edge,
             "status": "PROPOSED", "market_price": None,
             "verification_status": "NO_MARKET_PRICE",
             "game_available": int(r.game_pk) in game_map}
            for r in predictions.itertuples()]


def _research() -> list[dict[str, Any]]:
    findings = _db_frame("SELECT * FROM research_findings ORDER BY q_id")
    experiments = _db_frame("SELECT * FROM experiments ORDER BY exp_id")
    output = []
    if not findings.empty:
        for r in findings.itertuples():
            try:
                values = json.loads(r.stats_json) if r.stats_json else {}
            except (TypeError, json.JSONDecodeError):
                values = {}
            output.append({"id": r.q_id, "title": r.q_id, "status": r.verdict,
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
        return [{"id": "ISSUE-QUEUE-EMPTY", "title": "No issues recorded", "severity": "INFO",
                 "status": "OPEN", "description": "The queue is ready; no source snapshot has been loaded.",
                 "resolution": None}]
    return [{"id": r.issue_id, "title": r.issue_type, "severity": r.severity,
             "status": r.status, "description": r.description, "resolution": r.resolved_at}
            for r in issues.itertuples()]


def export(ledger_cap: int = LEDGER_CAP_DEFAULT) -> dict[str, Any]:
    db.init_db()
    strategies = _strategy_records()
    metrics = _metrics()
    if not metrics.empty:
        metric_keys = {(r.strategy_id, r.env) for r in metrics.itertuples() if int(r.total_bets) > 0}
        for strategy in strategies:
            if (strategy["sid"], strategy["env"]) in metric_keys and strategy.get("status") == "NOT_RUN":
                strategy["status"] = "BACKTESTED"
    leaderboard = _leaderboard(strategies, metrics)
    games = _db_frame("SELECT * FROM games")
    bets = _db_frame("SELECT * FROM bets")
    verified_bets = bets[bets.verification_status == "VERIFIED_PRICE"] if not bets.empty else bets
    real_pnl = float(verified_bets.pnl.fillna(0).sum()) if not verified_bets.empty else None
    env_breakdown = {}
    for env in ["REG", "POST", *ROUNDS]:
        subset = metrics[metrics.env == env] if not metrics.empty else pd.DataFrame()
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
    # A fetch manifest alone is not a usable model snapshot; normalized games
    # must pass ingest before the site leaves its safe empty mode.
    source_snapshot = bool((FEAT / "games.parquet").exists())
    summary = {
        "competition_name": "ARENA AI — MLB Autonomous MLB Research & Paper Competition",
        "as_of_date": TODAY,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by": "mlbcomp.web.export_static",
        "data_mode": "SOURCE_SNAPSHOT" if source_snapshot else "NO_SOURCE_SNAPSHOT",
        "current_season": 2026,
        "current_stage": "No source snapshot loaded — no upcoming wager is asserted" if not source_snapshot else "Source snapshot loaded; live availability requires a fresh run",
        "total_games_tracked": int(len(games)), "completed_games": int(games.home_score.notna().sum()) if not games.empty else 0,
        "upcoming_games": int(games.home_score.isna().sum()) if not games.empty else 0,
        "total_strategies": len(strategies), "total_simulated_bets": len(bets),
        "settled_bets": int((bets.status == "SETTLED").sum()) if not bets.empty else 0,
        "total_simulated_pnl": round(real_pnl, 2) if real_pnl is not None else None,
        "pnl_basis": "Verified observed quotes only; no price means no stake, PnL or ROI.",
        "total_upcoming_bets": len(_upcoming()), "total_open_positions": int(_db_frame("SELECT * FROM positions WHERE state='OPEN'").shape[0]),
        "total_kalshi_trades": int(_db_frame("SELECT * FROM immutable_ledger WHERE market='PREDICTION_MARKET'").shape[0]),
        "kalshi_status": "OPTIONAL — no contracts, quotes, fills or liquidity are created without verified API observations",
        "ledger_records_exported": len(_ledger(ledger_cap)), "ledger_records_in_db": len(bets),
        "ledger_note": "The immutable ledger is append-only; the static export may be capped for page size.",
        "top_performing_strategy": None, "top_pnl": None, "top_roi": None,
        "environment_breakdown": env_breakdown,
        "limitations": [
            "This checkout contains no fetched source snapshot unless data/raw or data/features is populated.",
            "Historical odds without an observed decision-time timestamp are not eligible for paper PnL.",
            "Postseason has distinct REG/POST/WC/DS/LCS/WS models and leaderboards; a small sample does not establish an edge.",
            "No real-money order connector exists; all positions are paper-only.",
        ],
    }
    _write("summary.json", summary)
    _write("leaderboard.json", leaderboard)
    _write("strategies.json", strategies)
    _write("bets_ledger.json", _ledger(ledger_cap))
    _write("upcoming_bets.json", _upcoming())
    _write("open_positions.json", [dict(r) for r in _db_frame("SELECT * FROM positions WHERE state='OPEN'").to_dict(orient="records")])
    _write("research_experiments.json", _research())
    _write("registry.json", registry_snapshot())
    _write("audit_checks.json", _audit())
    kalshi = _db_frame("SELECT * FROM immutable_ledger WHERE market='PREDICTION_MARKET' ORDER BY ledger_id DESC")
    kalshi_rows = kalshi.to_dict(orient="records") if not kalshi.empty else []
    _write("irregularities.json", _issues())
    _write("kalshi_trades.json", kalshi_rows)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger-cap", type=int, default=LEDGER_CAP_DEFAULT)
    args = parser.parse_args()
    result = export(args.ledger_cap)
    print(json.dumps({"data_mode": result["data_mode"], "strategies": result["total_strategies"],
                      "bets": result["total_simulated_bets"]}, indent=2))
