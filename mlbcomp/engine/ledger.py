"""Append-only paper ledger and market math.

The ledger refuses to create a wager without an observed, timestamped quote.
A model evaluation without a quote is a prediction/evaluation record, not a
bet.  It is therefore impossible for missing historical odds to turn into a
synthetic price, fill, liquidity value or ROI claim through this API.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from .. import db
from ..config import MAX_STAKE_PCT, KELLY_FRACTION, MARKETS, american_to_decimal, am_to_prob


@dataclass(frozen=True)
class ObservedQuote:
    source_id: str
    source_url: str | None
    observed_at: str
    available_at: str | None
    price_american: float
    market_type: str
    selection: str
    market_id: str | None = None
    bid: float | None = None
    ask: float | None = None
    liquidity: float | None = None
    available_size: float | None = None
    closing: bool = False
    verification_status: str = "VERIFIED"


@dataclass(frozen=True)
class Prediction:
    prediction_id: str
    strategy_version_id: str
    game_pk: int
    environment: str
    round_code: str | None
    decision_time: str
    data_cutoff_time: str | None
    selection: str
    model_probability: float
    fair_price: float
    required_price: float | None
    edge: float | None
    feature_snapshot_hash: str | None
    source_observation_ids: tuple[str, ...] = ()
    availability_status: str = "VERIFIED"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def kelly_stake(bankroll: float, probability: float, american_odds: float,
                fraction: float = KELLY_FRACTION, cap: float = MAX_STAKE_PCT) -> float:
    """Return a capped Kelly stake, never a negative or NaN stake."""
    try:
        bankroll, probability, american_odds, fraction, cap = map(
            float, (bankroll, probability, american_odds, fraction, cap))
    except (TypeError, ValueError):
        return 0.0
    if not (all(map(math.isfinite, (bankroll, probability, american_odds, fraction, cap)))
            and bankroll > 0 and 0 < probability < 1 and american_odds != 0
            and 0 <= fraction <= 1 and 0 <= cap <= 1):
        return 0.0
    decimal = american_to_decimal(american_odds)
    b = decimal - 1.0
    if not math.isfinite(b) or b <= 0:
        return 0.0
    raw = (b * probability - (1.0 - probability)) / b
    return round(bankroll * min(max(raw, 0.0) * fraction, cap), 2)


def quote_probability(quote: ObservedQuote) -> float:
    p = am_to_prob(quote.price_american)
    if not _finite(p):
        raise ValueError("quote has invalid American price")
    return float(p)


def _event_hash(payload: dict[str, Any], previous_hash: str | None) -> str:
    return db.stable_hash(payload, previous_hash)


def _last_hash(conn) -> str | None:
    row = conn.execute("SELECT entry_hash FROM immutable_ledger ORDER BY ledger_id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _append_event(conn, event_type: str, fields: dict[str, Any],
                  correction_of: int | None = None) -> int:
    """Insert a ledger event while holding the connection transaction."""
    now = fields.get("event_time") or utcnow()
    fields["event_time"] = now
    fields["event_type"] = event_type
    previous = _last_hash(conn)
    payload = {"event_type": event_type, "event_time": now, **fields,
               "correction_of": correction_of}
    entry_hash = _event_hash(payload, previous)
    columns = [
        "bet_id", "event_type", "event_time", "strategy_version_id", "game_pk",
        "environment", "round_code", "market", "selection", "source_id",
        "source_url", "observed_at", "availability_at", "price_american",
        "price_decimal", "implied_probability", "model_probability", "fair_price",
        "required_price", "edge", "stake", "bid", "ask", "liquidity", "available_size",
        "execution_status", "fill_quantity", "slippage", "closing_price", "result",
        "settlement", "pnl", "roi", "verification_status", "correction_of",
        "payload_json", "previous_hash", "entry_hash",
    ]
    values = [
        fields.get(c) for c in columns[:-3]
    ] + [db.jdump(payload), previous, entry_hash]
    conn.execute(
        f"INSERT INTO immutable_ledger ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        values,
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _timestamp(value: str | None, label: str) -> datetime:
    """Require an explicit timezone; never interpret local time as UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_prediction(prediction: Prediction) -> datetime:
    if not _finite(prediction.model_probability) or not 0 < prediction.model_probability < 1:
        raise ValueError("model probability must be between zero and one")
    decision = _timestamp(prediction.decision_time, "decision time")
    if prediction.data_cutoff_time is not None:
        if _timestamp(prediction.data_cutoff_time, "data cutoff") > decision:
            raise ValueError("data cutoff was after the prediction decision time")
    return decision


def record_prediction(prediction: Prediction) -> None:
    """Persist a point-in-time prediction, whether or not it becomes a wager."""
    _validate_prediction(prediction)
    db.init_db()
    with db.db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO predictions "
            "(prediction_id,strategy_version_id,game_pk,environment,round_code,decision_time,"
            "data_cutoff_time,selection,model_probability,fair_price,required_price,edge,"
            "feature_snapshot_hash,source_observation_ids,availability_status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (prediction.prediction_id, prediction.strategy_version_id, prediction.game_pk,
             prediction.environment, prediction.round_code, prediction.decision_time,
             prediction.data_cutoff_time, prediction.selection, prediction.model_probability,
             prediction.fair_price, prediction.required_price, prediction.edge,
             prediction.feature_snapshot_hash, db.jdump(list(prediction.source_observation_ids)),
             prediction.availability_status, utcnow()),
        )
    db.audit("prediction:recorded", prediction.prediction_id, entity_type="prediction",
             entity_id=prediction.prediction_id)


def record_wager(prediction: Prediction, quote: ObservedQuote, bankroll: float,
                 stake: float | None = None, bet_id: int | None = None) -> int:
    """Append a paper execution only for a verified observed quote.

    This is a simulation API: it never calls a bookmaker, exchange, Kalshi or
    any other order endpoint.
    """
    decision_ts = _validate_prediction(prediction)
    if prediction.availability_status != "VERIFIED":
        raise ValueError("wager requires verified prediction availability")
    if not _finite(bankroll) or float(bankroll) <= 0:
        raise ValueError("bankroll must be positive and finite")
    bankroll = float(bankroll)
    if quote.verification_status != "VERIFIED":
        raise ValueError("unverified quote cannot create a wager")
    if not quote.source_id or not quote.source_id.strip() or not quote.source_url or not quote.source_url.strip():
        raise ValueError("wager requires a source ID and URL/API locator")
    if not quote.selection or quote.selection != prediction.selection:
        raise ValueError("quote selection must match the prediction")
    if quote.market_type not in MARKETS:
        raise ValueError("unknown market type")
    if not _finite(quote.price_american) or quote.price_american == 0:
        raise ValueError("wager requires a non-zero observed American price")
    quote_ts = _timestamp(quote.observed_at, "quote observation")
    available_ts = _timestamp(quote.available_at, "quote availability")
    if quote_ts > decision_ts or available_ts > decision_ts:
        raise ValueError("quote observation/availability was after the prediction decision time")
    if available_ts < quote_ts:
        raise ValueError("quote availability cannot precede observation")
    if stake is None:
        stake = kelly_stake(bankroll, prediction.model_probability, quote.price_american)
    if not _finite(stake) or stake <= 0:
        raise ValueError("stake must be a positive, finite paper stake")
    if stake > bankroll * MAX_STAKE_PCT + 1e-6:
        raise ValueError("stake exceeds the configured paper-risk cap")
    decimal = american_to_decimal(quote.price_american)
    implied = quote_probability(quote)
    fields = {
        "event_type": "PAPER_OPEN",
        "strategy_version_id": prediction.strategy_version_id,
        "game_pk": prediction.game_pk,
        "environment": prediction.environment,
        "round_code": prediction.round_code,
        "market": quote.market_type,
        "selection": quote.selection,
        "source_id": quote.source_id,
        "source_url": quote.source_url,
        "observed_at": quote.observed_at,
        "availability_at": quote.available_at,
        "price_american": quote.price_american,
        "price_decimal": decimal,
        "implied_probability": implied,
        "model_probability": prediction.model_probability,
        "fair_price": prediction.fair_price,
        "required_price": prediction.required_price,
        "edge": prediction.edge,
        "stake": stake,
        "bid": quote.bid,
        "ask": quote.ask,
        "liquidity": quote.liquidity,
        "available_size": quote.available_size,
        "execution_status": "PAPER_FILLED",
        "fill_quantity": stake,
        "slippage": 0.0,
        "closing_price": quote.price_american if quote.closing else None,
        "verification_status": "VERIFIED",
    }
    db.init_db()
    with db.db() as conn:
        # Allocate the compatibility bet id first so the immutable event can
        # reference it at insert time.  The transaction rolls back both rows
        # if validation or hashing fails.
        cur = conn.execute(
            "INSERT INTO bets (game_pk,strategy_id,env,round_code,market,selection,model_prob,"
            "fair_price,market_price,edge,stake,made_at,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (prediction.game_pk, prediction.strategy_version_id, prediction.environment,
             prediction.round_code, quote.market_type, quote.selection,
             prediction.model_probability, prediction.fair_price, quote.price_american,
             prediction.edge, stake, prediction.decision_time, "OPEN"),
        )
        bet_id_new = int(cur.lastrowid)
        fields["bet_id"] = bet_id_new
        ledger_id = _append_event(conn, "PAPER_OPEN", fields)
        conn.execute(
            "INSERT INTO executions (execution_id,bet_id,executed_at,requested_price,fill_price,"
            "requested_size,filled_size,bid,ask,liquidity,slippage,partial_fill,verification_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"execution:{bet_id_new}", bet_id_new, prediction.decision_time, quote.price_american,
             quote.price_american, stake, stake, quote.bid, quote.ask, quote.liquidity,
             0.0, 0, "VERIFIED"),
        )
        conn.execute(
            "INSERT INTO positions (position_id,bet_id,strategy_version_id,opened_at,state,quantity,average_price,source_observation_ids) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"position:{bet_id_new}", bet_id_new, prediction.strategy_version_id,
             prediction.decision_time, "OPEN", stake, quote.price_american,
             db.jdump([quote.source_id])),
        )
    db.audit("paper:wager_opened", f"ledger_id={ledger_id}", entity_type="bet", entity_id=str(bet_id_new))
    return bet_id_new


def settle_wager(bet_id: int, result: str, source_id: str,
                 settlement: str | None = None, source_observation_id: str | None = None) -> int:
    """Append settlement; result must be externally observed W/L/P/V."""
    result = result.upper()
    if result not in {"W", "L", "P", "V"}:
        raise ValueError("settlement result must be W, L, P or V")
    db.init_db()
    with db.db() as conn:
        prior = conn.execute(
            "SELECT * FROM immutable_ledger WHERE bet_id=? AND event_type='PAPER_OPEN' "
            "ORDER BY ledger_id LIMIT 1", (bet_id,)).fetchone()
        if prior is None:
            raise KeyError(f"no immutable wager {bet_id}")
        if conn.execute(
            "SELECT 1 FROM immutable_ledger WHERE bet_id=? AND event_type='SETTLEMENT'",
            (bet_id,)).fetchone():
            raise ValueError("wager already has a settlement; append a correction instead")
        stake = float(prior["stake"] or 0.0)
        decimal = float(prior["price_decimal"] or 0.0)
        pnl = stake * (decimal - 1.0) if result == "W" else -stake if result == "L" else 0.0
        roi = pnl / stake if stake else None
        fields = {
            "bet_id": bet_id, "strategy_version_id": prior["strategy_version_id"],
            "game_pk": prior["game_pk"], "environment": prior["environment"],
            "round_code": prior["round_code"], "market": prior["market"],
            "selection": prior["selection"], "source_id": source_id,
            "observed_at": utcnow(), "price_american": prior["price_american"],
            "price_decimal": decimal, "implied_probability": prior["implied_probability"],
            "model_probability": prior["model_probability"], "fair_price": prior["fair_price"],
            "required_price": prior["required_price"], "edge": prior["edge"], "stake": stake,
            "execution_status": "PAPER_SETTLED", "fill_quantity": prior["fill_quantity"],
            "closing_price": prior["closing_price"], "result": result,
            "settlement": settlement or result, "pnl": pnl, "roi": roi,
            "verification_status": "VERIFIED", "payload_json": db.jdump({"source_observation_id": source_observation_id}),
        }
        ledger_id = _append_event(conn, "SETTLEMENT", fields)
        conn.execute("UPDATE bets SET status='SETTLED', result=?, pnl=? WHERE bet_id=?",
                     (result, pnl, bet_id))
        conn.execute(
            "INSERT INTO settlements (settlement_id,bet_id,settled_at,outcome,settlement_value,pnl,source_observation_id,verification_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"settlement:{bet_id}", bet_id, utcnow(), result,
             1.0 if result == "W" else 0.0 if result == "L" else None,
             pnl, source_observation_id, "VERIFIED"),
        )
        conn.execute("UPDATE positions SET state='CLOSED',closed_at=?,mark_price=?,realized_pnl=? WHERE bet_id=?",
                     (utcnow(), prior["price_american"], pnl, bet_id))
    db.audit("paper:wager_settled", f"ledger_id={ledger_id}; result={result}",
             entity_type="bet", entity_id=str(bet_id))
    return ledger_id


def append_backtest_event(conn, event_type: str, fields: dict[str, Any]) -> int:
    """Append a verified backtest event inside an existing DB transaction.

    This small public wrapper lets the chronological runner use the same hash
    chain as paper trading without pretending that a historical backtest was
    an exchange fill.
    """
    return _append_event(conn, event_type, dict(fields))


def record_correction(original_ledger_id: int, reason: str, replacement: dict[str, Any],
                      actor: str = "system") -> int:
    """Append a correction event and audit link; never edit the original row."""
    db.init_db()
    with db.db() as conn:
        original = conn.execute("SELECT * FROM immutable_ledger WHERE ledger_id=?",
                                (original_ledger_id,)).fetchone()
        if original is None:
            raise KeyError(f"unknown ledger event {original_ledger_id}")
        fields = {k: original[k] for k in (
            "bet_id", "strategy_version_id", "game_pk", "environment", "round_code",
            "market", "selection", "source_id", "source_url", "observed_at",
            "availability_at", "price_american", "price_decimal", "implied_probability",
            "model_probability", "fair_price", "required_price", "edge", "stake", "bid",
            "ask", "liquidity", "available_size", "execution_status", "fill_quantity",
            "slippage", "closing_price", "result", "settlement", "pnl", "roi",
            "verification_status")}
        fields.update(replacement)
        fields["payload_json"] = db.jdump({"reason": reason, "replacement": replacement})
        new_id = _append_event(conn, "CORRECTION", fields, correction_of=original_ledger_id)
        conn.execute(
            "INSERT INTO corrections (correction_id,entity_type,entity_id,corrected_at,reason,prior_hash,new_hash,actor) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"correction:{new_id}", "immutable_ledger", str(original_ledger_id), utcnow(), reason,
             original["entry_hash"], conn.execute("SELECT entry_hash FROM immutable_ledger WHERE ledger_id=?", (new_id,)).fetchone()[0], actor),
        )
    db.audit("ledger:correction", reason, actor=actor, entity_type="immutable_ledger", entity_id=str(original_ledger_id))
    return new_id


def verify_chain() -> dict[str, Any]:
    """Verify the hash chain without changing it."""
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM immutable_ledger ORDER BY ledger_id").fetchall()
    previous = None
    bad: list[int] = []
    for row in rows:
        payload = db.jload(row["payload_json"]) or {}
        expected = _event_hash(payload, previous)
        if row["previous_hash"] != previous or row["entry_hash"] != expected:
            bad.append(int(row["ledger_id"]))
        previous = row["entry_hash"]
    return {"rows": len(rows), "valid": not bad, "invalid_ledger_ids": bad}


def ledger_snapshot(limit: int | None = None) -> list[dict[str, Any]]:
    db.init_db()
    sql = "SELECT * FROM immutable_ledger ORDER BY ledger_id DESC" + (f" LIMIT {int(limit)}" if limit else "")
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(sql).fetchall()]
