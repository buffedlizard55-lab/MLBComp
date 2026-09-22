"""Adversarial integrity checks.

Checks validate controls and source-backed rows when present.  An empty data
store can pass a *gate* check, but it cannot produce a performance result;
those distinctions are published in the details column.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .. import db
from ..config import FEAT, ENVS, ROUNDS, ROOT
from ..engine.ledger import verify_chain
from ..engine.strategies import build_catalog


CHECKS = [
    "schema_required_tables", "source_registry_fields", "strategy_environment_isolation",
    "strategy_versions_present", "game_pk_unique", "score_integrity", "winner_integrity",
    "quote_price_integrity", "quote_availability_gate", "no_unverified_pnl",
    "ledger_hash_chain", "ledger_append_only_triggers", "prediction_cutoff_present",
    "postseason_round_codes", "series_state_pre_game", "no_settlement_without_result",
    "no_live_execution_connector", "issue_queue_available",
    "settlement_matches_scores", "quote_observations_verified",
    "clv_in_range", "series_needed_format",
]


def _outcome_from_scores(row) -> str | None:
    if pd.isna(row.home_score) or pd.isna(row.away_score):
        return None
    if row.home_score == row.away_score:
        return None
    home_won = row.home_score > row.away_score
    if row.market in {"ML", "F5_ML"}:
        selected_home = str(row.selection) == "HOME"
        return "W" if selected_home == home_won else "L"
    return None  # totals/other markets need a line; handled separately


def _check(conn, check_id: str, scope: str, passed: bool, details: str) -> bool:
    conn.execute(
        "INSERT OR REPLACE INTO verification_log (check_id,scope,passed,details,created_at) VALUES (?,?,?,?,?)",
        (check_id, scope, int(bool(passed)), details[:4000], db.utcnow()),
    )
    return bool(passed)


def _has(path: Path) -> bool:
    return path.exists()


def run_checks() -> dict[str, bool]:
    db.init_db()
    conn = db.connect()
    conn.execute("DELETE FROM verification_log")
    results: dict[str, bool] = {}

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"source_registry", "source_metadata", "games", "series_state", "strategies",
                "strategy_versions", "predictions", "market_quotes", "immutable_ledger",
                "data_issues", "audit_log"}
    results["schema_required_tables"] = _check(conn, "schema_required_tables", "schema",
        required <= tables, f"required tables present={sorted(required - tables) == []}; missing={sorted(required - tables)}")

    metadata = pd.read_sql("SELECT * FROM source_metadata", conn)
    fields = {"name", "url", "data_type", "historical_depth", "current_availability", "access_method",
              "cost", "restrictions", "licensing", "reliability", "granularity", "automation_capability",
              "verification_date", "verification_status", "limitations"}
    required_nonblank = fields - {"verification_date"}
    good_fields = fields <= set(metadata.columns) and (
        len(metadata) == 0 or metadata[list(required_nonblank)].notna().all().all()
    )
    # An unverified discovery record has no verification date by definition;
    # a date is required once status moves to VERIFIED/PARTIALLY_VERIFIED.
    if len(metadata) and "verification_date" in metadata:
        verified_without_date = metadata[metadata.verification_status.isin(["VERIFIED", "PARTIALLY_VERIFIED"])]\
            .verification_date.isna().sum()
        good_fields = good_fields and verified_without_date == 0
    results["source_registry_fields"] = _check(conn, "source_registry_fields", "sources", good_fields,
        f"registry rows={len(metadata)}, required metadata columns={sorted(fields)}; verification_date may be null only for NOT_VERIFIED discovery records")

    strategies = pd.read_sql("SELECT * FROM strategies", conn)
    env_ok = strategies.empty or strategies.env.isin(ENVS).all()
    results["strategy_environment_isolation"] = _check(conn, "strategy_environment_isolation", "strategies", env_ok,
        f"strategy rows={len(strategies)}; invalid environments={strategies.loc[~strategies.env.isin(ENVS), 'env'].tolist() if len(strategies) else []}")
    versions = pd.read_sql("SELECT * FROM strategy_versions", conn)
    version_ok = strategies.empty or strategies.strategy_id.isin(versions.strategy_id).all()
    results["strategy_versions_present"] = _check(conn, "strategy_versions_present", "strategies", version_ok,
        f"strategies={len(strategies)}, version rows={len(versions)}; every persisted strategy has a version")

    games = pd.DataFrame()
    game_path = FEAT / "games.parquet"
    if game_path.exists():
        games = pd.read_parquet(game_path)
    db_games = pd.read_sql("SELECT * FROM games", conn)
    if not games.empty:
        duplicate = int(games.game_pk.duplicated().sum())
        results["game_pk_unique"] = _check(conn, "game_pk_unique", "games", duplicate == 0,
            f"source-backed games={len(games)}, duplicate game_pk={duplicate}")
        completed_mask = games.get("completed", games.home_score.notna()).fillna(False).astype(bool)
        completed = games[completed_mask]
        score_bad = int(((completed.home_score < 0) | (completed.away_score < 0) | completed.home_score.isna() | completed.away_score.isna()).sum())
        results["score_integrity"] = _check(conn, "score_integrity", "games", score_bad == 0,
            f"completed rows={len(completed)}, invalid/non-negative score failures={score_bad}")
        winner_bad = 0
        if "winner_team_id" in completed:
            winner_bad = int(((completed.home_score > completed.away_score) & (completed.winner_team_id != completed.home_team_id) |
                              (completed.away_score > completed.home_score) & (completed.winner_team_id != completed.away_team_id)).sum())
        results["winner_integrity"] = _check(conn, "winner_integrity", "games", winner_bad == 0,
            f"winner/score mismatches={winner_bad}")
        round_bad = int((games.round_code.dropna().isin(ROUNDS) == False).sum()) if "round_code" in games else 0
    else:
        results["game_pk_unique"] = _check(conn, "game_pk_unique", "games", True, "no source-backed game snapshot loaded; gate is ready")
        results["score_integrity"] = _check(conn, "score_integrity", "games", True, "no completed game rows loaded; no score is asserted")
        results["winner_integrity"] = _check(conn, "winner_integrity", "games", True, "no completed game rows loaded; no winner is asserted")
        round_bad = 0

    quotes = pd.read_sql("SELECT * FROM market_quotes", conn)
    bad_quotes = 0
    if len(quotes):
        price_bad = ~quotes.price_american.apply(lambda x: isinstance(x, (int, float)) and math.isfinite(float(x)) and float(x) != 0)
        bad_quotes = int(price_bad.sum())
    results["quote_price_integrity"] = _check(conn, "quote_price_integrity", "markets", bad_quotes == 0,
        f"quote rows={len(quotes)}, invalid American prices={bad_quotes}; missing prices are not quote rows")
    # Every verified quote must have a timestamp. Unverified quotes may be
    # retained for research, but the PnL gate below excludes them.
    quote_gate = len(quotes) == 0 or not ((quotes.verification_status == "VERIFIED") & quotes.observed_at.isna()).any()
    results["quote_availability_gate"] = _check(conn, "quote_availability_gate", "markets", quote_gate,
        f"verified quotes without observed_at={int(((quotes.verification_status == 'VERIFIED') & quotes.observed_at.isna()).sum()) if len(quotes) else 0}")

    bets = pd.read_sql("SELECT * FROM bets", conn)
    unverified_pnl = 0
    if len(bets):
        mask = bets.verification_status.ne("VERIFIED_PRICE") & bets.pnl.fillna(0).abs().gt(1e-9)
        unverified_pnl = int(mask.sum())
    forward = pd.read_sql("SELECT * FROM forward_tests", conn)
    forward_bad = 0
    if len(forward):
        forward_bad = int(((forward.pnl.notna()) &
                           (forward.observed_price.isna() | forward.source_observation_ids.isna() |
                            forward.source_observation_ids.isin(["", "[]"]))).sum())
    results["no_unverified_pnl"] = _check(conn, "no_unverified_pnl", "ledger", unverified_pnl == 0 and forward_bad == 0,
        f"unverified bet rows with non-zero PnL={unverified_pnl}; forward rows missing observed price/source IDs={forward_bad}")

    chain = verify_chain()
    results["ledger_hash_chain"] = _check(conn, "ledger_hash_chain", "immutable_ledger", chain["valid"],
        f"ledger rows={chain['rows']}, invalid hashes={chain['invalid_ledger_ids']}")
    trigger_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    trigger_ok = {"immutable_ledger_no_update", "immutable_ledger_no_delete"} <= trigger_names
    results["ledger_append_only_triggers"] = _check(conn, "ledger_append_only_triggers", "immutable_ledger", trigger_ok,
        f"append-only triggers present={trigger_ok}")

    predictions = pd.read_sql("SELECT * FROM predictions", conn)
    cutoff_bad = int(predictions.data_cutoff_time.isna().sum()) if len(predictions) else 0
    results["prediction_cutoff_present"] = _check(conn, "prediction_cutoff_present", "predictions", cutoff_bad == 0,
        f"predictions={len(predictions)}, missing data_cutoff_time={cutoff_bad}")
    results["postseason_round_codes"] = _check(conn, "postseason_round_codes", "postseason", round_bad == 0,
        f"invalid round codes={round_bad}; valid={ROUNDS}; regular rows may have NULL")

    state = pd.read_sql("SELECT * FROM series_state", conn)
    state_bad = 0
    if len(state):
        state_bad = int(((state.wins_a_before < 0) | (state.wins_b_before < 0) | (state.game_number < 1)).sum())
    results["series_state_pre_game"] = _check(conn, "series_state_pre_game", "series_state", state_bad == 0,
        f"state rows={len(state)}, invalid pre-game counters={state_bad}")
    settlement_bad = int(((bets.status == "SETTLED") & ~bets.result.isin(["W", "L", "P", "V"])).sum()) if len(bets) else 0
    results["no_settlement_without_result"] = _check(conn, "no_settlement_without_result", "settlements", settlement_bad == 0,
        f"settled compatibility rows without W/L/P/V={settlement_bad}")

    # There is deliberately no SDK/client in the project that can place a real
    # order. Presence of common order-placement symbols would fail this check.
    forbidden = ["place" + "_order", "submit" + "_order", "create" + "_order",
                 "cancel" + "_order", "buy" + "_market", "sell" + "_market"]
    hits = []
    for path in ROOT.rglob("*.py"):
        if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
            if any(token in text for token in forbidden):
                hits.append(str(path.relative_to(ROOT)))
        except OSError:
            continue
    results["no_live_execution_connector"] = _check(conn, "no_live_execution_connector", "safety", not hits,
        f"forbidden order-placement symbols found in={hits}; paper ledger APIs only")
    issue_count = int(conn.execute("SELECT COUNT(*) FROM data_issues").fetchone()[0])
    results["issue_queue_available"] = _check(conn, "issue_queue_available", "data_quality", "data_issues" in tables,
        f"data_issues table present; open/recorded issues={issue_count}")

    # Recompute every scored ML bet's result from the source scores.  This
    # catches label inversions (e.g. away picks scored with a home-win y)
    # and any settlement drift between the games table and the ledger view.
    settle_mismatch = 0
    settle_checked = 0
    if len(bets) and len(db_games):
        scored = bets[bets.result.isin(["W", "L"]) & bets.market.isin(["ML", "F5_ML"])].copy()
        if len(scored):
            merged = scored.merge(
                db_games[["game_pk", "home_score", "away_score"]], on="game_pk", how="inner")
            for r in merged.itertuples():
                expected = _outcome_from_scores(r)
                if expected is None:
                    continue
                settle_checked += 1
                if expected != r.result:
                    settle_mismatch += 1
    results["settlement_matches_scores"] = _check(conn, "settlement_matches_scores", "settlements",
        settle_mismatch == 0,
        f"scored ML rows re-derived from games: checked={settle_checked}, mismatches={settle_mismatch}")

    # Every VERIFIED quote must point at a VERIFIED source observation.
    quotes_bad = 0
    if len(quotes):
        obs = pd.read_sql("SELECT observation_id FROM source_observations WHERE verification_status='VERIFIED'", conn)
        obs_ids = set(obs.observation_id)
        vq = quotes[quotes.verification_status == "VERIFIED"]
        quotes_bad = int(sum(1 for sid in vq.source_observation_id if sid not in obs_ids))
    results["quote_observations_verified"] = _check(conn, "quote_observations_verified", "markets",
        quotes_bad == 0,
        f"verified quotes missing a VERIFIED source observation={quotes_bad}")

    # CLV is a probability difference: must lie in [-1, 1].
    clv_bad = 0
    if len(bets) and "clv" in bets:
        clv_vals = bets.clv.dropna()
        clv_bad = int(((clv_vals < -1.0) | (clv_vals > 1.0)).sum())
    results["clv_in_range"] = _check(conn, "clv_in_range", "markets", clv_bad == 0,
        f"clv values outside [-1,1]={clv_bad}")

    # Series tables must agree with the shared round_needed() format rules —
    # guards against the WC single-game vs best-of-three bug class.
    needed_bad = 0
    series_tbl = pd.read_sql("SELECT * FROM series", conn)
    if len(series_tbl):
        from ..config import round_needed
        for r in series_tbl.itertuples():
            try:
                expected = round_needed(r.round_code, int(r.season))
            except ValueError:
                needed_bad += 1
                continue
            if int(r.needed_a) != expected or int(r.needed_b) != expected:
                needed_bad += 1
    results["series_needed_format"] = _check(conn, "series_needed_format", "postseason",
        needed_bad == 0,
        f"series rows with wrong wins-needed={needed_bad} (shared round_required rules; "
        "WC is single-game 2012-2019/2021, best-of-3 in 2020 and 2022+)")

    conn.commit()
    conn.close()
    db.audit("verify:completed", f"checks={len(results)} passed={sum(results.values())}")
    return results


if __name__ == "__main__":
    import json
    print(json.dumps(run_checks(), indent=2))
