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
        # Pre-fetch series_state for Q10-13 where available
        try:
            state = db.query_df("SELECT * FROM series_state")
        except Exception:
            state = pd.DataFrame()
        # Calibration for Q09 late-season comparison (Brier)
        try:
            cal = db.query_df("SELECT strategy_id, brier, n_games FROM calibration")
        except Exception:
            cal = pd.DataFrame()
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
                by_round = {}
                for rnd in ("WC", "DS", "LCS", "WS"):
                    sub = post[post.round_code == rnd]
                    if len(sub):
                        by_round[rnd] = {"n": int(len(sub)),
                                         "home_win_rate": _safe((sub.home_score > sub.away_score).mean())}
                findings.append(_finding(qid, "POST", _comparison_verdict(delta, len(a), len(b)), len(a),
                    {"post_home_win_rate": _safe(a.mean()) if len(a) else None,
                     "reg_home_win_rate": _safe(b.mean()) if len(b) else None, "delta": delta,
                     "by_round": by_round},
                    "Home-win frequency is descriptive; no causal postseason assumption is made.", provenance))
            elif qid == "Q09":
                # Late-season form: compare lateform vs elo Brier if available
                if not cal.empty and "MLB_REG_LATEFORM_001" in cal.strategy_id.values and "MLB_REG_ELO_001" in cal.strategy_id.values:
                    lt = cal[cal.strategy_id=="MLB_REG_LATEFORM_001"].iloc[0]
                    elo = cal[cal.strategy_id=="MLB_REG_ELO_001"].iloc[0]
                    delta = float(lt.brier - elo.brier) if pd.notna(lt.brier) and pd.notna(elo.brier) else None
                    verdict = _comparison_verdict(delta, int(lt.n_games), int(elo.n_games)) if delta is not None else "INCONCLUSIVE"
                    findings.append(_finding(qid, "REG", verdict, int(lt.n_games),
                        {"lateform_brier": _safe(lt.brier), "elo_brier": _safe(elo.brier), "delta": delta, "elo_n": int(elo.n_games), "lateform_n": int(lt.n_games)},
                        "Late-season Brier vs Elo baseline; delta measured point-in-time, not a market edge.", provenance))
                else:
                    findings.append(_finding(qid, "REG", "DATA_UNAVAILABLE", 0, {}, "Late-season comparison requires completed calibration for both models.", provenance))
            elif qid == "Q10":
                if not state.empty:
                    # games.parquet has its own game_number (doubleheader), so use suffixes
                    merged = post.merge(state, left_on="game_pk", right_on="game_pk", how="inner", suffixes=("", "_state"))
                    if len(merged) >= 30:
                        elim = merged[(merged.elimination_a==1) | (merged.elimination_b==1)]
                        non = merged[(merged.elimination_a==0) & (merged.elimination_b==0)]
                        a = (elim.home_score > elim.away_score).mean() if len(elim) else None
                        b = (non.home_score > non.away_score).mean() if len(non) else None
                        delta = float(a - b) if a is not None and b is not None else None
                        findings.append(_finding(qid, "POST", _comparison_verdict(delta, len(elim), len(non)), len(merged),
                            {"elimination_home_win_rate": _safe(a), "non_elim_home_win_rate": _safe(b), "delta": delta, "elim_n": len(elim), "non_elim_n": len(non)},
                            "Elimination vs non-elimination home win rate (descriptive, not causal; Elo not yet partialled).", provenance))
                    else:
                        findings.append(_finding(qid, "POST", "INSUFFICIENT_SAMPLE", len(merged), {"post_n": len(merged)}, "Series-state sample too small for win-rate comparison.", provenance))
                else:
                    findings.append(_finding(qid, "POST", "DATA_UNAVAILABLE", 0, {}, "Series-state table absent; cannot evaluate without pre-game counters.", provenance))
            elif qid == "Q11":
                if not state.empty:
                    merged = post.merge(state, left_on="game_pk", right_on="game_pk", how="inner", suffixes=("", "_state"))
                    if len(merged) >= 10:
                        clinch = merged[(merged.clinch_a==1) | (merged.clinch_b==1)]
                        elim = merged[(merged.elimination_a==1) | (merged.elimination_b==1)]
                        findings.append(_finding(qid, "POST", "EVALUATED" if len(clinch) and len(elim) else "INSUFFICIENT_SAMPLE", len(merged),
                            {"clinch_games": len(clinch), "elimination_games": len(elim), "post_n": len(merged)},
                            "Counts of clinching/elimination games; win-rate effects in Q10.", provenance))
                    else:
                        findings.append(_finding(qid, "POST", "INSUFFICIENT_SAMPLE", len(merged), {"post_n": len(merged)}, "Too few postseason games to separate clinch/elimination.", provenance))
                else:
                    findings.append(_finding(qid, "POST", "DATA_UNAVAILABLE", 0, {}, "Series-state required.", provenance))
            elif qid == "Q13":
                if not state.empty and "days_rest_home" in state.columns:
                    merged = post.merge(state, left_on="game_pk", right_on="game_pk", how="inner", suffixes=("", "_state"))
                    merged = merged.dropna(subset=["days_rest_home", "days_rest_away"])
                    if len(merged) >= 30:
                        merged["rest_diff"] = merged["days_rest_home"] - merged["days_rest_away"]
                        win = merged[merged.home_score > merged.away_score]["rest_diff"].mean()
                        loss = merged[merged.home_score < merged.away_score]["rest_diff"].mean()
                        delta = float(win - loss) if pd.notna(win) and pd.notna(loss) else None
                        findings.append(_finding(qid, "POST", _comparison_verdict(delta, len(merged), len(merged)), len(merged),
                            {"home_win_rest_diff_mean": _safe(win), "home_loss_rest_diff_mean": _safe(loss), "delta": delta},
                            "Rest difference vs home win (descriptive).", provenance))
                    else:
                        findings.append(_finding(qid, "POST", "INSUFFICIENT_SAMPLE", len(merged), {"post_n": len(merged)}, "Insufficient postseason games with rest data.", provenance))
                else:
                    findings.append(_finding(qid, "POST", "DATA_UNAVAILABLE", 0, {}, "Rest/travel columns not available.", provenance))
            elif qid == "Q04":
                pg_path = FEAT / "pitcher_game.parquet"
                if pg_path.exists() and len(completed):
                    pg = pd.read_parquet(pg_path)
                    starter = pg[pg.is_starter == 1].merge(
                        completed[["game_pk", "round_code"]], on="game_pk", how="inner")
                    starter = starter[starter.batters_faced > 0]
                    reg_sp = starter[starter.round_code.isna()].batters_faced.astype(float)
                    post_sp = starter[starter.round_code.notna()].batters_faced.astype(float)
                    delta = float(post_sp.mean() - reg_sp.mean()) if len(reg_sp) and len(post_sp) else None
                    verdict = _comparison_verdict(delta, len(reg_sp), len(post_sp)) if delta is not None else "DATA_UNAVAILABLE"
                    findings.append(_finding(qid, "POST", verdict, int(len(post_sp)),
                        {"reg_starter_bf_mean": _safe(reg_sp.mean()) if len(reg_sp) else None,
                         "reg_starter_bf_std": _safe(reg_sp.std(ddof=1)) if len(reg_sp) > 1 else None,
                         "post_starter_bf_mean": _safe(post_sp.mean()) if len(post_sp) else None,
                         "post_starter_bf_std": _safe(post_sp.std(ddof=1)) if len(post_sp) > 1 else None,
                         "delta_batters_faced": delta, "reg_n": int(len(reg_sp)), "post_n": int(len(post_sp))},
                        "Starter leash measured as batters faced by the first pitcher of the game "
                        "(play-by-play derived) in REG vs POST; descriptive, not causal.", provenance))
                else:
                    findings.append(_finding(qid, "POST", "DATA_UNAVAILABLE", 0, {},
                        "pitcher_game table absent; starter leash cannot be measured.", provenance))
            elif qid == "Q05":
                ge_path = FEAT / "game_events.parquet"
                tp_path = FEAT / "team_pitching_game.parquet"
                if ge_path.exists() and len(completed):
                    ge = pd.read_parquet(ge_path).merge(
                        completed[["game_pk", "round_code"]], on="game_pk", how="inner")
                    reg_g = ge[ge.round_code.isna()].pitchers_used.astype(float)
                    post_g = ge[ge.round_code.notna()].pitchers_used.astype(float)
                    delta = float(post_g.mean() - reg_g.mean()) if len(reg_g) and len(post_g) else None
                    bp_stats = {}
                    if tp_path.exists():
                        tp = pd.read_parquet(tp_path).merge(
                            completed[["game_pk", "round_code"]], on="game_pk", how="inner")
                        tp["bp_share"] = tp.bp_bf / tp.bf_total.clip(lower=1)
                        reg_bp = tp[tp.round_code.isna()].bp_share.astype(float)
                        post_bp = tp[tp.round_code.notna()].bp_share.astype(float)
                        bp_stats = {"reg_bp_bf_share": _safe(reg_bp.mean()) if len(reg_bp) else None,
                                    "post_bp_bf_share": _safe(post_bp.mean()) if len(post_bp) else None,
                                    "delta_bp_share": (float(post_bp.mean() - reg_bp.mean())
                                                       if len(reg_bp) and len(post_bp) else None)}
                    verdict = _comparison_verdict(delta, len(reg_g), len(post_g)) if delta is not None else "DATA_UNAVAILABLE"
                    findings.append(_finding(qid, "POST", verdict, int(len(post_g)),
                        {"reg_pitchers_used_mean": _safe(reg_g.mean()) if len(reg_g) else None,
                         "post_pitchers_used_mean": _safe(post_g.mean()) if len(post_g) else None,
                         "delta_pitchers_used": delta, "reg_n": int(len(reg_g)), "post_n": int(len(post_g)),
                         **bp_stats},
                        "Bullpen usage measured by pitchers used per game and bullpen share of "
                        "batters faced (play-by-play derived); descriptive, not causal.", provenance))
                else:
                    findings.append(_finding(qid, "POST", "DATA_UNAVAILABLE", 0, {},
                        "game_events table absent; bullpen usage cannot be measured.", provenance))
            elif qid == "Q12":
                if not state.empty:
                    merged = post.merge(state, left_on="game_pk", right_on="game_pk", how="inner", suffixes=("", "_state"))
                    # series_state game_number is _state when collision with schedule doubleheader number
                    gn_col = "game_number_state" if "game_number_state" in merged.columns else "game_number"
                    if gn_col not in merged.columns:
                        gn_col = "game_number_y" if "game_number_y" in merged.columns else "game_number"
                    merged = merged[merged[gn_col].notna()] if gn_col in merged.columns else merged
                    if len(merged) >= 30 and gn_col in merged.columns:
                        merged["margin"] = merged.home_score - merged.away_score
                        by_number = {}
                        for gn, sub in merged.groupby(merged[gn_col].astype(int)):
                            by_number[int(gn)] = {"n": int(len(sub)),
                                                  "home_win_rate": _safe((sub.margin > 0).mean()),
                                                  "mean_margin": _safe(sub.margin.mean())}
                        findings.append(_finding(qid, "POST", "EVALUATED", int(len(merged)),
                            {"by_game_number": by_number},
                            "Home win rate and mean margin by series game number (descriptive; "
                            "home-field pattern confounds raw rates).", provenance))
                    else:
                        findings.append(_finding(qid, "POST", "INSUFFICIENT_SAMPLE" if len(merged) else "DATA_UNAVAILABLE",
                            int(len(merged)) if len(merged) else 0,
                            {"post_n": int(len(merged))}, "Too few postseason games for game-number split or missing column.", provenance))
                else:
                    findings.append(_finding(qid, "POST", "DATA_UNAVAILABLE", 0, {},
                        "Series-state table absent.", provenance))
            elif qid == "Q14":
                odds_path = FEAT / "odds.parquet"
                if odds_path.exists():
                    o = pd.read_parquet(odds_path)
                    o = o[o.verification_status == "VERIFIED"].copy()
                    if len(o) >= 30 and {"home_odds_open", "home_odds_close"} <= set(o.columns):
                        from ..config import am_to_prob, devig_two
                        rows_mv = []
                        for r in o.dropna(subset=["home_odds_open", "home_odds_close"]).itertuples():
                            ho, ao = devig_two(am_to_prob(r.home_odds_open), am_to_prob(r.away_odds_open))
                            hc, ac = devig_two(am_to_prob(r.home_odds_close), am_to_prob(r.away_odds_close))
                            if pd.notna(ho) and pd.notna(hc):
                                rows_mv.append({"game_pk": r.game_pk,
                                                "move": float(hc) - float(ho),
                                                "close_home": float(hc)})
                        mv = pd.DataFrame(rows_mv)
                        if len(mv):
                            gm = mv.merge(completed[["game_pk", "round_code", "home_score", "away_score"]],
                                          on="game_pk", how="inner")
                            gm["home_won"] = (gm.home_score > gm.away_score).astype(int)
                            calib = {"n": int(len(gm)),
                                     "mean_abs_move": _safe(gm["move"].abs().mean()),
                                     "fav_at_close_win_rate": _safe(
                                         ((gm.close_home > 0.5).astype(int) == gm.home_won).mean()),
                                     "brier_close": _safe(((gm.close_home - gm.home_won) ** 2).mean())}
                            by_env = {}
                            for name, mask in (("REG", gm.round_code.isna()),
                                               ("POST", gm.round_code.notna())):
                                sub = gm[mask]
                                if len(sub):
                                    by_env[name] = {"n": int(len(sub)),
                                                    "mean_abs_move": _safe(sub["move"].abs().mean()),
                                                    "brier_close": _safe(((sub.close_home - sub.home_won) ** 2).mean())}
                            findings.append(_finding(qid, "ALL", "EVALUATED", int(len(gm)),
                                {**calib, "by_env": by_env},
                                "Verified open/close rows only: movement size, closing-line favorite "
                                "hit rate and closing de-vigged Brier by environment. This measures "
                                "market efficiency, not a strategy's edge.", provenance))
                        else:
                            findings.append(_finding(qid, "ALL", "DATA_UNAVAILABLE", 0, {},
                                "No verified open/close pairs after joins.", provenance))
                    else:
                        findings.append(_finding(qid, "ALL", "DATA_UNAVAILABLE", 0, {},
                            f"Verified quote rows={int(len(o))}; open/close columns required.", provenance))
                else:
                    findings.append(_finding(qid, "ALL", "DATA_UNAVAILABLE", 0, {},
                        "odds.parquet absent.", provenance))
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
        # Refresh findings for this run; previous run's rows are preserved in git history via exported JSON
        conn.execute("DELETE FROM research_findings")
        conn.executemany(
            "INSERT INTO research_findings (q_id,env,round_code,verdict,n,stats_json,evidence,created_at,provenance,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            findings,
        )
        # Keep experiments deterministic: replace previous A–E on each run
        conn.execute("DELETE FROM experiments WHERE exp_id LIKE 'EXP_%'")
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
            experiments.append((base_id, name, "POST", None, m.get("n", 0), m.get("brier"),
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
