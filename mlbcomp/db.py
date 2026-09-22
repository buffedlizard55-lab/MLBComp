"""Persistent, auditable storage for MLBComp.

The database is the source of truth for a rebuilt competition.  Published JSON
is only a read-only projection.  Wager facts are append-only in
``immutable_ledger``; a correction is another row linked to the original, not
an UPDATE or DELETE.  Every value that can influence a prediction has a
provenance pointer and a data-availability status.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd

from .config import DB_PATH

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS source_registry (
  source_id TEXT PRIMARY KEY,
  url TEXT,
  description TEXT,
  coverage TEXT,
  verified INTEGER NOT NULL DEFAULT 0,
  reject_reason TEXT,
  fetched_at TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS source_metadata (
  source_id TEXT PRIMARY KEY REFERENCES source_registry(source_id),
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  data_type TEXT,
  historical_depth TEXT,
  current_availability TEXT,
  access_method TEXT,
  cost TEXT,
  restrictions TEXT,
  licensing TEXT,
  reliability TEXT,
  granularity TEXT,
  automation_capability TEXT,
  verification_date TEXT,
  verification_status TEXT NOT NULL DEFAULT 'NOT_VERIFIED',
  limitations TEXT,
  last_http_status INTEGER,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS source_observations (
  observation_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_locator TEXT,
  record_key TEXT,
  field_name TEXT,
  raw_value TEXT,
  derived_value TEXT,
  retrieval_time TEXT NOT NULL,
  availability_time TEXT,
  checksum TEXT,
  verification_status TEXT NOT NULL,
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_obs_record ON source_observations(record_key, field_name);

CREATE TABLE IF NOT EXISTS teams (
  team_id INTEGER PRIMARY KEY, name TEXT, abbr TEXT, league TEXT
);
CREATE TABLE IF NOT EXISTS players (
  player_id INTEGER PRIMARY KEY, name TEXT, bats TEXT, throws TEXT,
  birth_date TEXT, active_from TEXT, active_to TEXT
);
CREATE TABLE IF NOT EXISTS seasons (
  year INTEGER PRIMARY KEY, reg_games INTEGER, po_games INTEGER,
  first_game TEXT, last_game TEXT
);
CREATE TABLE IF NOT EXISTS games (
  game_pk INTEGER PRIMARY KEY, season INTEGER, game_date TEXT, start_utc TEXT,
  game_type TEXT, round_code TEXT, series_key TEXT, home_team_id INTEGER,
  away_team_id INTEGER, home_score INTEGER, away_score INTEGER, status TEXT,
  venue_name TEXT, winner_team_id INTEGER, extra_innings INTEGER, home_win INTEGER
);
CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date, season);
CREATE INDEX IF NOT EXISTS idx_games_round ON games(season, round_code);
CREATE TABLE IF NOT EXISTS series (
  series_key TEXT PRIMARY KEY, season INTEGER, round_code TEXT, league TEXT,
  team_a_id INTEGER, team_b_id INTEGER, seed_a INTEGER, seed_b INTEGER,
  wins_a INTEGER DEFAULT 0, wins_b INTEGER DEFAULT 0, needed_a INTEGER,
  needed_b INTEGER, status TEXT, winner_id INTEGER
);
CREATE TABLE IF NOT EXISTS series_state (
  game_pk INTEGER PRIMARY KEY, series_key TEXT, round_code TEXT,
  game_number INTEGER, wins_a_before INTEGER, wins_b_before INTEGER,
  elimination_a INTEGER, elimination_b INTEGER, clinch_a INTEGER, clinch_b INTEGER,
  series_tied INTEGER, games_remaining INTEGER, days_rest_home REAL,
  days_rest_away REAL, travel_km_home REAL, travel_km_away REAL,
  home_adv_games_left INTEGER
);
CREATE TABLE IF NOT EXISTS rosters (
  roster_id TEXT PRIMARY KEY, season INTEGER, team_id INTEGER, player_id INTEGER,
  role TEXT, start_date TEXT, end_date TEXT, source_observation_id TEXT
);
CREATE TABLE IF NOT EXISTS lineups (
  lineup_id TEXT PRIMARY KEY, game_pk INTEGER, team_id INTEGER, batting_order INTEGER,
  player_id INTEGER, position TEXT, announced_at TEXT, available_at TEXT,
  source_observation_id TEXT, verification_status TEXT
);
CREATE TABLE IF NOT EXISTS starting_pitchers (
  record_id TEXT PRIMARY KEY, game_pk INTEGER, team_id INTEGER, player_id INTEGER,
  announced_at TEXT, available_at TEXT, confirmed_at TEXT, source_observation_id TEXT,
  verification_status TEXT
);
CREATE TABLE IF NOT EXISTS bullpen_usage (
  record_id TEXT PRIMARY KEY, game_pk INTEGER, team_id INTEGER, player_id INTEGER,
  pitches INTEGER, batters_faced INTEGER, leverage REAL, used_at TEXT,
  source_observation_id TEXT, verification_status TEXT
);
CREATE TABLE IF NOT EXISTS injuries (
  injury_id TEXT PRIMARY KEY, player_id INTEGER, team_id INTEGER, status TEXT,
  body_part TEXT, reported_at TEXT, effective_from TEXT, effective_to TEXT,
  source_observation_id TEXT, verification_status TEXT
);
CREATE TABLE IF NOT EXISTS statistics (
  stat_id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT, season INTEGER,
  game_pk INTEGER, as_of_time TEXT, stat_name TEXT, value REAL,
  source_observation_id TEXT, verification_status TEXT
);
CREATE TABLE IF NOT EXISTS statcast_events (
  event_id TEXT PRIMARY KEY, game_pk INTEGER, event_time TEXT, pitcher_id INTEGER,
  batter_id INTEGER, pitch_type TEXT, velocity REAL, spin_rate REAL,
  launch_speed REAL, launch_angle REAL, outcome TEXT, source_observation_id TEXT,
  verification_status TEXT
);
CREATE TABLE IF NOT EXISTS weather_observations (
  weather_id TEXT PRIMARY KEY, game_pk INTEGER, observed_at TEXT, temperature REAL,
  wind_speed REAL, wind_direction REAL, humidity REAL, precipitation REAL,
  roof_status TEXT, source_observation_id TEXT, verification_status TEXT
);
CREATE TABLE IF NOT EXISTS umpires (
  assignment_id TEXT PRIMARY KEY, game_pk INTEGER, umpire_id INTEGER,
  crew_role TEXT, assigned_at TEXT, source_observation_id TEXT,
  verification_status TEXT
);
CREATE TABLE IF NOT EXISTS travel_rest (
  record_id TEXT PRIMARY KEY, game_pk INTEGER, team_id INTEGER, rest_days REAL,
  travel_km REAL, timezone_change REAL, doubleheader INTEGER,
  source_observation_id TEXT, verification_status TEXT
);

CREATE TABLE IF NOT EXISTS markets (
  market_id TEXT PRIMARY KEY, game_pk INTEGER, environment TEXT, round_code TEXT,
  market_type TEXT, selection TEXT, line REAL, contract_id TEXT,
  settlement_rule TEXT, source_id TEXT, verification_status TEXT
);
CREATE TABLE IF NOT EXISTS market_quotes (
  quote_id TEXT PRIMARY KEY, market_id TEXT, observed_at TEXT NOT NULL,
  available_at TEXT, price_american REAL, price_decimal REAL, implied_probability REAL,
  bid REAL, ask REAL, liquidity REAL, available_size REAL, closing_flag INTEGER DEFAULT 0,
  source_observation_id TEXT, verification_status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quotes_market_time ON market_quotes(market_id, observed_at);

CREATE TABLE IF NOT EXISTS model_versions (
  version_id TEXT PRIMARY KEY, model TEXT, env TEXT, created_at TEXT,
  config_json TEXT, notes TEXT, parent_version TEXT, feature_cutoff_rule TEXT,
  training_window TEXT, validation_window TEXT, test_window TEXT,
  status TEXT DEFAULT 'DRAFT'
);
CREATE TABLE IF NOT EXISTS strategy_versions (
  strategy_version_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
  version_label TEXT NOT NULL, environment TEXT NOT NULL, round_code TEXT,
  hypothesis TEXT, data_requirements TEXT, entry_rule TEXT, required_price_rule TEXT,
  sizing_rule TEXT, settlement_rule TEXT, test_plan TEXT, limitations TEXT,
  parent_version TEXT, change_summary TEXT, created_at TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS strategies (
  strategy_id TEXT PRIMARY KEY, name TEXT, env TEXT, model TEXT, market TEXT,
  hypothesis TEXT, status TEXT, created_at TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
  prediction_id TEXT PRIMARY KEY, strategy_version_id TEXT, game_pk INTEGER,
  environment TEXT, round_code TEXT, decision_time TEXT NOT NULL,
  data_cutoff_time TEXT, selection TEXT, model_probability REAL, fair_price REAL,
  required_price REAL, edge REAL, feature_snapshot_hash TEXT,
  source_observation_ids TEXT, availability_status TEXT, result TEXT,
  created_at TEXT
);

-- Legacy-compatible view of wagers.  It is populated only from observed quotes.
CREATE TABLE IF NOT EXISTS bets (
  bet_id INTEGER PRIMARY KEY AUTOINCREMENT, game_pk INTEGER, strategy_id TEXT,
  env TEXT, season INTEGER, round_code TEXT, market TEXT, selection TEXT,
  model_prob REAL, fair_price REAL, market_price REAL, edge REAL, stake REAL,
  made_at TEXT, status TEXT, result TEXT, pnl REAL, closing_price REAL, clv REAL,
  series_state TEXT, synthetic_pnl REAL, synthetic INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bets_strategy ON bets(strategy_id, env);
CREATE INDEX IF NOT EXISTS idx_bets_game ON bets(game_pk);

CREATE TABLE IF NOT EXISTS immutable_ledger (
  ledger_id INTEGER PRIMARY KEY AUTOINCREMENT, bet_id INTEGER, event_type TEXT NOT NULL,
  event_time TEXT NOT NULL, strategy_version_id TEXT, game_pk INTEGER,
  environment TEXT, round_code TEXT, market TEXT, selection TEXT, source_id TEXT,
  source_url TEXT, observed_at TEXT, availability_at TEXT, price_american REAL,
  price_decimal REAL, implied_probability REAL, model_probability REAL,
  fair_price REAL, required_price REAL, edge REAL, stake REAL, bid REAL, ask REAL,
  liquidity REAL, available_size REAL, execution_status TEXT, fill_quantity REAL,
  slippage REAL, closing_price REAL, result TEXT, settlement TEXT, pnl REAL,
  roi REAL, verification_status TEXT NOT NULL, correction_of INTEGER,
  payload_json TEXT, previous_hash TEXT, entry_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_ledger_bet ON immutable_ledger(bet_id, event_time);
CREATE TRIGGER IF NOT EXISTS immutable_ledger_no_update
BEFORE UPDATE ON immutable_ledger BEGIN SELECT RAISE(ABORT, 'immutable ledger: append a correction'); END;
CREATE TRIGGER IF NOT EXISTS immutable_ledger_no_delete
BEFORE DELETE ON immutable_ledger BEGIN SELECT RAISE(ABORT, 'immutable ledger: rows cannot be deleted'); END;
CREATE TABLE IF NOT EXISTS positions (
  position_id TEXT PRIMARY KEY, bet_id INTEGER, strategy_version_id TEXT,
  opened_at TEXT, closed_at TEXT, state TEXT, quantity REAL, average_price REAL,
  mark_price REAL, realized_pnl REAL, source_observation_ids TEXT
);
CREATE TABLE IF NOT EXISTS executions (
  execution_id TEXT PRIMARY KEY, bet_id INTEGER, executed_at TEXT, requested_price REAL,
  fill_price REAL, requested_size REAL, filled_size REAL, bid REAL, ask REAL,
  liquidity REAL, slippage REAL, partial_fill INTEGER, source_observation_id TEXT,
  verification_status TEXT
);
CREATE TABLE IF NOT EXISTS settlements (
  settlement_id TEXT PRIMARY KEY, bet_id INTEGER, settled_at TEXT, outcome TEXT,
  settlement_value REAL, pnl REAL, source_observation_id TEXT, verification_status TEXT
);
CREATE TABLE IF NOT EXISTS corrections (
  correction_id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT, corrected_at TEXT,
  reason TEXT, prior_hash TEXT, new_hash TEXT, actor TEXT
);
CREATE TABLE IF NOT EXISTS bankroll (
  strategy_id TEXT, env TEXT, seq INTEGER, game_date TEXT, balance REAL,
  roi REAL, max_dd REAL, PRIMARY KEY(strategy_id, env, seq)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
  run_id TEXT PRIMARY KEY, run_type TEXT, started_at TEXT, finished_at TEXT,
  data_cutoff TEXT, code_version TEXT, config_json TEXT, status TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS forward_tests (
  forward_id TEXT PRIMARY KEY, run_id TEXT, strategy_version_id TEXT, game_pk INTEGER,
  decision_time TEXT, information_available TEXT, model_probability REAL,
  fair_price REAL, observed_price REAL, required_price REAL, stake REAL,
  result TEXT, pnl REAL, status TEXT, source_observation_ids TEXT
);
CREATE TABLE IF NOT EXISTS experiments (
  exp_id TEXT PRIMARY KEY, name TEXT, env TEXT, round_code TEXT, n INTEGER,
  brier REAL, logloss REAL, roi REAL, pnl REAL, details_json TEXT, created_at TEXT,
  status TEXT DEFAULT 'NOT_RUN'
);
CREATE TABLE IF NOT EXISTS research_questions (q_id TEXT PRIMARY KEY, question TEXT);
CREATE TABLE IF NOT EXISTS research_findings (
  finding_id INTEGER PRIMARY KEY AUTOINCREMENT, q_id TEXT, env TEXT, round_code TEXT,
  verdict TEXT, n INTEGER, stats_json TEXT, evidence TEXT, created_at TEXT,
  provenance TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS calibration (
  strategy_id TEXT, env TEXT, n_games INTEGER, brier REAL, log_loss REAL,
  updated_at TEXT, PRIMARY KEY(strategy_id, env)
);
CREATE TABLE IF NOT EXISTS data_issues (
  issue_id TEXT PRIMARY KEY, issue_type TEXT, severity TEXT, status TEXT,
  entity_type TEXT, entity_id TEXT, description TEXT, source_ids TEXT,
  detected_at TEXT, resolved_at TEXT, correction_id TEXT
);
CREATE TABLE IF NOT EXISTS verification_log (
  check_id TEXT PRIMARY KEY, scope TEXT, passed INTEGER, details TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  log_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT,
  entity_type TEXT, entity_id TEXT, details TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, sql_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Migrate databases created by the pre-ledger prototype without
        # destroying them.  New code writes the append-only ledger.
        _ensure_columns(conn, "bets", {"synthetic_pnl": "REAL", "synthetic": "INTEGER DEFAULT 0", "run_id": "TEXT", "prediction_id": "TEXT", "verification_status": "TEXT", "price_tier": "TEXT", "required_price": "REAL", "quote_source_id": "TEXT", "quote_source_url": "TEXT", "quote_source_observation_id": "TEXT", "quote_observed_at": "TEXT", "quote_available_at": "TEXT", "quote_price_american": "REAL"})
        _ensure_columns(conn, "research_findings", {"round_code": "TEXT", "provenance": "TEXT", "status": "TEXT"})
        _ensure_columns(conn, "experiments", {"status": "TEXT DEFAULT 'NOT_RUN'"})
        _ensure_columns(conn, "audit_log", {"log_id": "INTEGER", "entity_type": "TEXT", "entity_id": "TEXT"})
        _ensure_columns(conn, "model_versions", {"parent_version": "TEXT", "feature_cutoff_rule": "TEXT", "training_window": "TEXT", "validation_window": "TEXT", "test_window": "TEXT", "status": "TEXT"})
        _ensure_columns(conn, "source_metadata", {"notes": "TEXT"})
        conn.commit()


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def audit(action: str, details: str = "", actor: str = "system",
          entity_type: str | None = None, entity_id: str | None = None) -> None:
    init_db()
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, actor, action, entity_type, entity_id, details) "
            "VALUES (?,?,?,?,?,?)",
            (utcnow(), actor, action, entity_type, entity_id, str(details)[:4000]),
        )


def jdump(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True, separators=(",", ":"))


def jload(value: str | None):
    return json.loads(value) if value else None


def pd_isna(value: Any) -> bool:
    try:
        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except (TypeError, ValueError):
        return value is None


def sql_rows(df) -> list[tuple]:
    """Convert a DataFrame to SQLite-safe tuples without turning strings to NaN."""
    import numpy as np
    rows = []
    for row in df.itertuples(index=False):
        rows.append(tuple(
            None if value is None or pd_isna(value)
            else value.item() if isinstance(value, np.generic) else value
            for value in row
        ))
    return rows


def query_df(sql: str, params: Iterable[Any] = ()) -> pd.DataFrame:
    init_db()
    with connect() as conn:
        return pd.read_sql_query(sql, conn, params=tuple(params))


def record_issue(issue_id: str, issue_type: str, description: str,
                 severity: str = "MEDIUM", status: str = "OPEN",
                 entity_type: str | None = None, entity_id: str | None = None,
                 source_ids: list[str] | None = None) -> None:
    init_db()
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO data_issues "
            "(issue_id, issue_type, severity, status, entity_type, entity_id, "
            "description, source_ids, detected_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (issue_id, issue_type, severity, status, entity_type, entity_id,
             description[:4000], jdump(source_ids or []), utcnow()),
        )


def stable_hash(payload: Any, previous_hash: str | None = None) -> str:
    material = jdump({"previous_hash": previous_hash, "payload": payload}).encode()
    return hashlib.sha256(material).hexdigest()
