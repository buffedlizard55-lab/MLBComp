"""Autonomous research loop with explicit data gates.

Research questions become findings only from source-backed rows.  Missing
pitchers, lineups, weather, umpire, market or liquidity fields are reported as
DATA_UNAVAILABLE/NOT_RUN, never imputed into a positive conclusion.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import db
from ..config import FEAT, RESEARCH_QUESTIONS
from .evaluation import brier_score, log_loss


def _safe(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _finding(qid: str, env: str, verdict: str, n: int, stats: dict,
             evidence: str, provenance: str, round_code: str | None = None) -> tuple:
    return (qid, env, round_code, verdict, int(n), db.jdump(stats), evidence,
            db.utcnow(), provenance, "COMPLETED" if verdict not in {"DATA_UNAVAILABLE", "NOT_RUN"} else verdict)


def _comparison_verdict(delta: float | None, n_a: int, n_b: int) -> str:
    if delta is None or min(n_a, n_b) < 30:
        return "INSUFFICIENT_SAMPLE"
    # A conservative effect-size screen, not a claim of statistical
    # significance.  The exact uncertainty is retained in stats_json.
    return "INCONCLUSIVE" if abs(delta) < 0.25 else "REQUIRES_REPLICATION"


def _load_results() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    path = FEAT / "games.parquet"
    if not path.exists():
        return None
    games = pd.read_parquet(path)
    completed_mask = games.get("completed", games.home_score.notna()).fillna(False).astype(bool)
    completed = games[completed_mask].copy()
    completed = completed[completed.home_score.notna() & completed.away_score.notna()]
    return games, completed


def _experiment_metrics(strategy_id: str, env: str | None = None) -> dict[str, Any]:
    sql = "SELECT b.model_prob,b.result,b.market_price,b.stake,b.pnl,b.verification_status FROM bets b WHERE b.strategy_id=?"
    params: list[Any] = [strategy_id]
    if env:
        sql += " AND b.env=?"
        params.append(env)
    frame = db.query_df(sql, params)
    frame = frame[frame.result.isin(["W", "L"])] if not frame.empty else frame
    if frame.empty:
        return {"n": 0, "status": "NOT_RUN"}
    y = (frame.result == "W").astype(int).to_numpy()
    p = frame.model_prob.astype(float).to_numpy()
    verified = frame[frame.verification_status == "VERIFIED_PRICE"]
    out = {"n": len(frame), "win_rate": float(y.mean()),
           "brier": brier_score(p, y), "log_loss": log_loss(p, y),
           "verified_n": int(len(verified)), "status": "EVALUATED"}
    if len(verified) and verified.stake.sum() > 0:
        out["verified_roi"] = float(verified.pnl.fillna(0).sum() / verified.stake.sum())
        out["verified_pnl"] = float(verified.pnl.fillna(0).sum())
    else:
        out["verified_roi"] = None
        out["verified_pnl"] = None
    return out


def _experiment_metrics_many(strategy_ids: list[str], env: str | None = None) -> dict[str, Any]:
    if not strategy_ids:
        return {"n": 0, "status": "NOT_RUN"}
    placeholders = ",".join("?" for _ in strategy_ids)
    sql = f"SELECT model_prob,result,market_price,stake,pnl,verification_status FROM bets WHERE strategy_id IN ({placeholders})"
    params: list[Any] = list(strategy_ids)
    if env:
        sql += " AND env=?"
        params.append(env)
    frame = db.query_df(sql, params)
    frame = frame[frame.result.isin(["W", "L"])] if not frame.empty else frame
    if frame.empty:
        return {"n": 0, "status": "NOT_RUN"}
    y = (frame.result == "W").astype(int).to_numpy()
    p = frame.model_prob.astype(float).to_numpy()
    verified = frame[frame.verification_status == "VERIFIED_PRICE"]
    stake = float(verified.stake.fillna(0).sum()) if len(verified) else 0.0
    return {"n": len(frame), "win_rate": float(y.mean()),
            "brier": brier_score(p, y), "log_loss": log_loss(p, y),
            "verified_n": int(len(verified)),
            "verified_roi": float(verified.pnl.fillna(0).sum() / stake) if stake else None,
            "verified_pnl": float(verified.pnl.fillna(0).sum()) if stake else None,
            "status": "EVALUATED"}


def build_research() -> dict[str, Any]:
    db.init_db()
    with db.db() as conn:
        for qid, question in RESEARCH_QUESTIONS:
            conn.execute("INSERT OR IGNORE INTO research_questions (q_id,question) VALUES (?,?)", (qid, question))

    loaded = _load_results()
    findings: list[tuple] = []
    provenance = "No source-backed feature snapshot is available in this checkout."
    if loaded is None:
        for qid, _ in RESEARCH_QUESTIONS:
            findings.append(_finding(qid, "POST" if qid in {f"Q{x:02d}" for x in range(1, 19)} else "ALL",
                                     "DATA_UNAVAILABLE", 0, {},
                                     "Not run: source data required by this question is unavailable.", provenance))
    else:
        games, completed = loaded
        provenance = "games.parquet; completed rows only; point-in-time feature and source gates apply."
        is_post = completed.round_code.notna()
        post = completed[is_post]
        reg = completed[~is_post]
        for qid, _ in RESEARCH_QUESTIONS:
            # Environment facts that require only verified scores.
            if qid == "Q01":
                a = (post.home_score + post.away_score).to_numpy(dtype=float)
                b = (reg.home_score + reg.away_score).to_numpy(dtype=float)
                delta = float(a.mean() - b.mean()) if len(a) and len(b) else None
                findings.append(_finding(qid, "POST", _comparison_verdict(delta, len(a), len(b)), len(a),
                    {"post_mean_total": _safe(a.mean()) if len(a) else None,
                     "reg_mean_total": _safe(b.mean()) if len(b) else None, "delta": delta,
                     "post_n": len(a), "reg_n": len(b)},
                    "Observed run totals only; this is descriptive and requires multi-season replication.", provenance))
            elif qid == "Q02":
                a = (post.home_score - post.away_score).to_numpy(dtype=float)
                b = (reg.home_score - reg.away_score).to_numpy(dtype=float)
                delta = float(a.std(ddof=1) - b.std(ddof=1)) if len(a) > 1 and len(b) > 1 else None
                findings.append(_finding(qid, "POST", _comparison_verdict(delta, len(a), len(b)), len(a),
                    {"post_margin_std": _safe(a.std(ddof=1)) if len(a) > 1 else None,
                     "reg_margin_std": _safe(b.std(ddof=1)) if len(b) > 1 else None, "delta": delta},
                    "Observed run margins only; not a market-edge claim.", provenance))
            elif qid == "Q03":
                a = (post.home_score > post.away_score).astype(int).to_numpy()
                b = (reg.home_score > reg.away_score).astype(int).to_numpy()
                delta = float(a.mean() - b.mean()) if len(a) and len(b) else None
                findings.append(_finding(qid, "POST", _comparison_verdict(delta, len(a), len(b)), len(a),
                    {"post_home_win_rate": _safe(a.mean()) if len(a) else None,
                     "reg_home_win_rate": _safe(b.mean()) if len(b) else None, "delta": delta},
                    "Home-win frequency is descriptive; no causal postseason assumption is made.", provenance))
            elif qid == "Q19":
                # A market family is available only if quotes carry an explicit
                # verified status; the absence is an observed data result.
                quotes = db.query_df("SELECT COUNT(*) AS n FROM market_quotes WHERE verification_status='VERIFIED'")
                n = int(quotes.iloc[0].n) if len(quotes) else 0
                findings.append(_finding(qid, "ALL", "EVALUATED" if n else "DATA_UNAVAILABLE", n,
                    {"verified_quote_rows": n},
                    "Historical market coverage is counted from verified quote records only.",
                    "market_quotes.source_observation_id; no quote is inferred."))
            else:
                findings.append(_finding(qid, "POST" if qid not in {"Q19", "Q20"} else "ALL",
                    "NOT_RUN", 0, {},
                    "Queued for a dedicated experiment; required point-in-time variables or a registered model comparison are not present.",
                    provenance))

    with db.db() as conn:
        conn.executemany(
            "INSERT INTO research_findings (q_id,env,round_code,verdict,n,stats_json,evidence,created_at,provenance,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            findings,
        )
        experiments = []
        pairs = [("A", "MLB_POST_XREG_001", "Model A — regular-season transfer"),
                 ("B", "MLB_POST_ADJUSTED_001", "Model B — regular season plus postseason adjustment"),
                 ("C", "MLB_POST_SERIESSTATE_001", "Model C — dedicated postseason/series state"),
                 ("D", "MLB_POST_WC_ELO_001", "Model D — round-specific models"),
                 ("E", "MLB_POST_HIERARCHICAL_001", "Model E — hierarchical partial pooling")]
        for exp_id, strategy_id, name in pairs:
            if exp_id == "D":
                round_ids = [f"MLB_POST_{r}_ELO_001" for r in ("WC", "DS", "LCS", "WS")]
                m = _experiment_metrics_many(round_ids)
                detail = {"strategy_ids": round_ids, **m}
            else:
                m = _experiment_metrics(strategy_id, "POST")
                detail = {"strategy_id": strategy_id, **m}
            base_id = f"EXP_{exp_id}"
            exp_key = base_id if conn.execute("SELECT 1 FROM experiments WHERE exp_id=?", (base_id,)).fetchone() is None \
                else f"{base_id}:{db.utcnow()}"
            experiments.append((exp_key, name, "POST", None, m.get("n", 0), m.get("brier"),
                                m.get("log_loss"), m.get("verified_roi"), m.get("verified_pnl"),
                                db.jdump(detail), db.utcnow(), m.get("status", "NOT_RUN")))
        conn.executemany(
            "INSERT INTO experiments (exp_id,name,env,round_code,n,brier,logloss,roi,pnl,details_json,created_at,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            experiments,
        )
    db.audit("research:completed", f"questions={len(findings)} experiments=5")
    return {"questions": len(findings), "experiments": 5,
            "data_available": loaded is not None,
            "status": "COMPLETED" if loaded is not None else "DATA_UNAVAILABLE"}


if __name__ == "__main__":
    print(build_research())
