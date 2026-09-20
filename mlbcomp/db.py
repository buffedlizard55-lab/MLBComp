"""SQLite persistence for the competition ledger, models, research, verification.

Design (spec §25): every record is independently auditable. Raw game/feature
data lives in parquet (data/features/); the DB holds the competition state:
games, series, series-state, strategies, model versions, bets, bankroll,
research findings, experiments, verification log and audit trail.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import pandas as pd

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_registry (
  source_id   TEXT PRIMARY KEY,
  url         TEXT,
  description TEXT,
  coverage    TEXT,
  verified    INTEGER,
  reject_reason TEXT,
  fetched_at  TEXT,
  notes       TEXT
);
CREATE TABLE IF NOT EXISTS teams (
  team_id   INTEGER PRIMARY KEY,
  name      TEXT,
  abbr      TEXT,
  league    TEXT
);
CREATE TABLE IF NOT EXISTS seasons (
  year        INTEGER PRIMARY KEY,
  reg_games   INTEGER,
  po_games    INTEGER,
  first_game  TEXT,
  last_game   TEXT
);
CREATE TABLE IF NOT EXISTS games (
  game_pk        INTEGER PRIMARY KEY,
  season         INTEGER,
  game_date      TEXT,
  start_utc      TEXT,
  game_type      TEXT,          -- R / F / D / L / W
  round_code     TEXT,          -- NULL / WC / DS / LCS / WS
  series_key     TEXT,          -- e.g. 2025|WC|AL|2
  home_team_id   INTEGER,
  away_team_id   INTEGER,
  home_score     INTEGER,
  away_score     INTEGER,
  status         TEXT,          -- F(final) / A/active / P preview / S suspended
  venue_name     TEXT,
  winner_team_id INTEGER,
  extra_innings  INTEGER,
  home_win       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date, season);
CREATE INDEX IF NOT EXISTS idx_games_round ON games(season, round_code);
CREATE TABLE IF NOT EXISTS series (
  series_key   TEXT PRIMARY KEY,
  season       INTEGER,
  round_code   TEXT,
  league       TEXT,
  team_a_id    INTEGER,          -- host of first game(s)
  team_b_id    INTEGER,
  seed_a       INTEGER,
  seed_b       INTEGER,
  wins_a       INTEGER DEFAULT 0,
  wins_b       INTEGER DEFAULT 0,
  needed_a     INTEGER,          -- wins needed to clinch (format-dependent)
  needed_b     INTEGER,
  status       TEXT,             -- SCHEDULED / IN_PROGRESS / COMPLETED
  winner_id    INTEGER
);
CREATE TABLE IF NOT EXISTS series_state (
  game_pk          INTEGER PRIMARY KEY,
  series_key       TEXT,
  round_code       TEXT,
  game_number      INTEGER,
  wins_a_before    INTEGER,
  wins_b_before    INTEGER,
  elimination_a    INTEGER,
  elimination_b    INTEGER,
  clinch_a         INTEGER,
  clinch_b         INTEGER,
  series_tied      INTEGER,
  games_remaining  INTEGER,
  days_rest_home   REAL,
  days_rest_away   REAL,
  travel_km_home   REAL,
  travel_km_away   REAL,
  home_adv_games_left INTEGER
);
CREATE TABLE IF NOT EXISTS model_versions (
  version_id   TEXT PRIMARY KEY,
  model        TEXT,
  env          TEXT,
  created_at   TEXT,
  config_json  TEXT,
  notes        TEXT
);
CREATE TABLE IF NOT EXISTS strategies (
  strategy_id  TEXT PRIMARY KEY,
  name         TEXT,
  env          TEXT,
  model        TEXT,
  market       TEXT,             -- ML (moneyline) / TOTAL
  hypothesis   TEXT,
  status       TEXT,             -- ACTIVE / REJECTED / INSUFFICIENT_SAMPLE / FORWARD_TESTING / NO_DATA
  created_at   TEXT,
  notes        TEXT
);
CREATE TABLE IF NOT EXISTS bets (
  bet_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  game_pk       INTEGER,
  strategy_id   TEXT,
  env           TEXT,
  season        INTEGER,
  round_code    TEXT,
  market        TEXT,
  selection     TEXT,            -- team id or OVER/UNDER
  model_prob    REAL,
  fair_price    REAL,
  market_price  REAL,
  edge          REAL,
  stake         REAL,
  made_at       TEXT,
  status        TEXT,            -- OPEN / SETTLED / VOID
  result        TEXT,            -- W / L / P
  pnl           REAL,
  closing_price REAL,
  clv           REAL,
  series_state  TEXT,
  faircoin_pnl  REAL,            -- fair-coin proxy PnL (+100, 2% flat)
  synthetic     INTEGER DEFAULT 0  -- 1 = settled vs synthetic line (no real odds)
);
CREATE INDEX IF NOT EXISTS idx_bets_strategy ON bets(strategy_id, env);
CREATE INDEX IF NOT EXISTS idx_bets_game ON bets(game_pk);
CREATE TABLE IF NOT EXISTS bankroll (
  strategy_id TEXT,
  env         TEXT,
  seq         INTEGER,
  game_date   TEXT,
  balance     REAL,
  roi         REAL,
  max_dd      REAL,
  PRIMARY KEY (strategy_id, env, seq)
);
CREATE TABLE IF NOT EXISTS research_questions (
  q_id       TEXT PRIMARY KEY,
  question   TEXT
);
CREATE TABLE IF NOT EXISTS research_findings (
  finding_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  q_id        TEXT,
  env         TEXT,
  verdict     TEXT,              -- SUPPORTED / CONTRADICTED / NO_MEANINGFUL_EVIDENCE / DATA_UNAVAILABLE / INCONCLUSIVE
  n           INTEGER,
  stats_json  TEXT,
  evidence    TEXT,
  created_at  TEXT
);
CREATE TABLE IF NOT EXISTS experiments (
  exp_id       TEXT PRIMARY KEY,
  name         TEXT,
  env          TEXT,
  round_code   TEXT,
  n            INTEGER,
  brier        REAL,
  logloss      REAL,
  roi          REAL,
  pnl          REAL,
  details_json TEXT,
  created_at   TEXT
);
CREATE TABLE IF NOT EXISTS calibration (
  strategy_id  TEXT,
  env          TEXT,
  n_games      INTEGER,
  brier        REAL,
  log_loss     REAL,
  updated_at   TEXT,
  PRIMARY KEY (strategy_id, env)
);
CREATE TABLE IF NOT EXISTS verification_log (
  check_id    TEXT PRIMARY KEY,
  scope       TEXT,
  passed      INTEGER,
  details     TEXT,
  created_at  TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  ts        TEXT,
  actor     TEXT,
  action    TEXT,
  details   TEXT
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


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    # lightweight migrations for pre-existing DB files
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bets)")}
    if "faircoin_pnl" not in cols:
        conn.execute("ALTER TABLE bets ADD COLUMN faircoin_pnl REAL")
    if "synthetic" not in cols:
        conn.execute("ALTER TABLE bets ADD COLUMN synthetic INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


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


def audit(action: str, details: str = "", actor: str = "system") -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, actor, action, details) VALUES (?,?,?,?)",
            (utcnow(), actor, action, details[:2000]),
        )


def jdump(obj) -> str:
    return json.dumps(obj, default=str)


def jload(s):
    return json.loads(s) if s else None


def sql_rows(df) -> list:
    """DataFrame -> list of tuples safe for sqlite (None for NA, py scalars)."""
    import numpy as np
    rows = []
    for row in df.itertuples(index=False):
        rows.append(tuple(
            None if (v is None or pd_isna(v))
            else (v.item() if isinstance(v, np.generic) else v)
            for v in row))
    return rows


def pd_isna(v):
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return v is None
