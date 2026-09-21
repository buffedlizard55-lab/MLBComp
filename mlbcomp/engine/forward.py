"""Forward-test and paper-trade recording.

Forward tests record what was known at a decision timestamp.  Paper positions
add an observed quote and bounded execution details.  Neither function has an
order-placement integration.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .. import db
from .ledger import ObservedQuote, Prediction, record_prediction, record_wager


def record_forward_test(prediction: Prediction, run_id: str,
                        observed_price: float | None = None, required_price: float | None = None,
                        stake: float = 0.0, result: str | None = None,
                        pnl: float | None = None, source_observation_ids: list[str] | None = None) -> str:
    """Record an out-of-sample prediction; missing price remains NULL."""
    source_ids = list(source_observation_ids or prediction.source_observation_ids)
    if observed_price is None and (stake or 0) != 0:
        raise ValueError("a forward-test stake requires an observed price")
    if pnl is not None:
        if observed_price is None or not source_ids:
            raise ValueError("forward-test PnL requires an observed price and source observation IDs")
        db.init_db()
        placeholders = ",".join("?" for _ in source_ids)
        with db.connect() as check_conn:
            verified = check_conn.execute(
                f"SELECT COUNT(*) FROM source_observations WHERE observation_id IN ({placeholders}) "
                "AND verification_status='VERIFIED'", tuple(source_ids)).fetchone()[0]
        if int(verified) != len(set(source_ids)):
            raise ValueError("forward-test PnL requires VERIFIED source observations")
    record_prediction(prediction)
    forward_id = f"forward:{prediction.prediction_id}"
    status = "SETTLED" if result in {"W", "L", "P", "V"} else "OPEN"
    db.init_db()
    with db.db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO forward_tests "
            "(forward_id,run_id,strategy_version_id,game_pk,decision_time,information_available,"
            "model_probability,fair_price,observed_price,required_price,stake,result,pnl,status,source_observation_ids) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (forward_id, run_id, prediction.strategy_version_id, prediction.game_pk,
             prediction.decision_time, db.jdump({"data_cutoff_time": prediction.data_cutoff_time,
                                                 "availability_status": prediction.availability_status}),
             prediction.model_probability, prediction.fair_price, observed_price,
             required_price or prediction.required_price, stake, result, pnl, status,
             db.jdump(source_ids)),
        )
    db.audit("forward:recorded", forward_id, entity_type="forward_test", entity_id=forward_id)
    return forward_id


def paper_trade(prediction: Prediction, quote: ObservedQuote, bankroll: float,
                stake: float | None = None) -> int:
    """Open a bounded paper position.  This function cannot place live orders."""
    if quote.market_type not in {"ML", "RL", "TOTAL", "TEAM_TOTAL", "F5_ML", "F5_TOTAL",
                                 "NRFI", "YRFI", "PLAYER_PROP", "PITCHER_PROP", "ALT_LINE",
                                 "LIVE", "EXCHANGE", "PREDICTION_MARKET", "FUTURES"}:
        raise ValueError("unknown market type")
    return record_wager(prediction, quote, bankroll, stake=stake)


def create_run(run_id: str, run_type: str, data_cutoff: str | None,
               config: dict[str, Any], notes: str = "") -> None:
    db.init_db()
    with db.db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO backtest_runs "
            "(run_id,run_type,started_at,data_cutoff,config_json,status,notes) VALUES (?,?,?,?,?,?,?)",
            (run_id, run_type, db.utcnow(), data_cutoff, db.jdump(config), "RUNNING", notes),
        )


def finish_run(run_id: str, status: str = "COMPLETED", notes: str = "") -> None:
    db.init_db()
    with db.db() as conn:
        conn.execute("UPDATE backtest_runs SET finished_at=?,status=?,notes=? WHERE run_id=?",
                     (db.utcnow(), status, notes, run_id))
    db.audit("run:finished", f"{run_id}:{status}", entity_type="run", entity_id=run_id)
