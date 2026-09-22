"""Enrich the committed static snapshot with the competition layer.

This script never alters verified numbers:

* ``strategies.json`` is rebuilt from the versioned python catalog.  Every
  previously published entry must be present with identical contract fields;
  the assertion fails the run if a strategy was edited in place.
* ``leaderboard.json`` keeps every existing row verbatim (verified metrics are
  immutable here).  Newly catalogued strategies receive explicit NO_DATA rows —
  no performance is synthesized for a strategy that has not been backtested.
* ``competitors.json`` / ``competitions.json`` are regenerated deterministically
  from the committed ledger export via ``mlbcomp.engine.competition``.
* ``summary.json`` only gains competition metadata; existing fields are
  asserted unchanged except ``generated_at``/count fields documented below.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlbcomp.config import DATA, ENV_LABEL, STARTING_BANKROLL  # noqa: E402
from mlbcomp.engine.competition import (  # noqa: E402
    DEFAULT_SEED, ENGINE_VERSION, build_payloads,
)
from mlbcomp.engine.strategies import build_catalog  # noqa: E402

PRICING_NOTE = ("No verified quote means EVAL/PROPOSED only; PnL and ROI "
                "remain null. Brier/log loss score model calibration on "
                "settled outcomes regardless of price.")

def _read(name: str):
    return json.loads((DATA / name).read_text())


def _write(name: str, value) -> None:
    (DATA / name).write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n")


def _no_data_leaderboard_row(record: dict) -> dict:
    """Mirror export_static._leaderboard's NO_DATA branch shape exactly."""
    sid = record["sid"]
    return {
        "id": sid, "username": sid, "name": record["name"],
        "category": record["model"], "env": record["env"],
        "env_label": ENV_LABEL.get(record["env"], record["env"]),
        "version": record.get("version", "v1"), "model": record["model"],
        "market": record["market"], "total_bets": 0, "settled_bets": 0,
        "eval_picks": 0, "wins": 0, "losses": 0, "pushes": 0,
        "win_rate": None, "total_pnl": None, "verified_bets": 0,
        "verified_stake": None, "verified_pnl": None, "verified_roi": None,
        "roi": None, "initial_bankroll": STARTING_BANKROLL,
        "current_bankroll": None, "max_drawdown": None, "equity_curve": [],
        "brier": None, "log_loss": None, "calibration_n": 0,
        "status": record.get("status", "NOT_RUN"), "metric_status": "NO_DATA",
        "pricing_note": PRICING_NOTE,
        "experiment_alias": sid.startswith("MLB_POST_MODEL_"),
        "alias_of": (record.get("extra") or {}).get("alias_of"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default=DEFAULT_SEED,
                        help="recorded RNG seed for competition window draws")
    parser.add_argument("--check", action="store_true",
                        help="validate invariants without writing files")
    args = parser.parse_args()

    summary = _read("summary.json")
    old_strategies = _read("strategies.json")
    old_leaderboard = _read("leaderboard.json")
    ledger_rows = _read("bets_ledger.json")

    # 1. strategies.json: strictly additive.  Previously published entries are
    # the export-promoted, human-audited records and stay byte-identical; the
    # python catalog is only allowed to append new ids (versioning contract).
    catalog_records = [s.to_record() for s in build_catalog()]
    catalog_by_id = {r["sid"]: r for r in catalog_records}
    old_ids = [s.get("sid") for s in old_strategies]
    errors = []
    for sid in old_ids:
        if sid not in catalog_by_id:
            errors.append(f"strategy {sid} would disappear from the catalog")
    strategies_next = list(old_strategies)
    new_ids = []
    appended_keys = set(old_strategies[0]) if old_strategies else set()
    for record in catalog_records:
        if record["sid"] in old_ids:
            continue
        if set(record) != appended_keys:
            errors.append(f"new strategy {record['sid']} field set does not match "
                          f"the published record shape")
        strategies_next.append(record)
        new_ids.append(record["sid"])
    if len([r["sid"] for r in strategies_next]) != len({r["sid"] for r in strategies_next}):
        errors.append("duplicate strategy ids after merge")
    if errors:
        for err in errors:
            print(f"REFUSING: {err}", file=sys.stderr)
        sys.exit(1)

    # 2. leaderboard.json: existing rows verbatim + explicit NO_DATA rows.
    leaderboard = list(old_leaderboard)
    for sid in new_ids:
        leaderboard.append(_no_data_leaderboard_row(catalog_by_id[sid]))
    assert len(leaderboard) == len(strategies_next)

    # 3. Competition payloads from the committed ledger export.
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    competitors, competitions = build_payloads(
        strategies_next, leaderboard, ledger_rows, seed=args.seed,
        generated_at=generated_at)

    # 4. summary.json: competition metadata + refreshed *catalog-derived*
    # strategy counts.  Verified performance fields stay byte-identical.
    summary_next = dict(summary)
    for key in ("environment_breakdown", "unique_environment_breakdown"):
        breakdown = summary.get(key)
        if not isinstance(breakdown, dict):
            continue
        updated = {}
        for env, row in breakdown.items():
            row = dict(row)
            alias_excluded = key.startswith("unique")
            row["strategies"] = sum(
                1 for r in strategies_next if r["env"] == env
                and not (alias_excluded
                         and (str(r.get("sid", "")).startswith("MLB_POST_MODEL_")
                              or (r.get("extra") or {}).get("alias_of"))))
            updated[env] = row
        summary_next[key] = updated
    summary_next.update({
        "total_strategies": len(strategies_next),
        "total_competition_entrants": len(competitors["entrants"]),
        "total_competitions": len(competitions["competitions"]),
        "competition_seed": args.seed,
        "competition_generator": ENGINE_VERSION,
        "competitions_built_at": generated_at,
        "competitions_note": (
            "Simulated competitions: randomized window start dates (recorded seed) over the "
            "committed verified export; entrants are personas bound to transparent strategy "
            "slices; standings are deterministic re-aggregations of real records; PnL is "
            "published only where VERIFIED_PRICE rows exist in the window."
        ),
    })
    # Fields this script owns and regenerates by design; every other committed
    # summary field must be preserved exactly.  Inside the environment
    # breakdowns only the catalog-derived `strategies` count may move.
    regenerated = {"total_strategies", "total_competition_entrants", "total_competitions",
                   "competition_seed", "competition_generator", "competitions_built_at",
                   "competitions_note", "environment_breakdown",
                   "unique_environment_breakdown"}
    for key, value in summary.items():
        if key in regenerated:
            continue
        if summary_next.get(key) != value:
            print(f"REFUSING: summary field {key!r} would change", file=sys.stderr)
            sys.exit(1)
    for key in ("environment_breakdown", "unique_environment_breakdown"):
        before, after = summary.get(key) or {}, summary_next.get(key) or {}
        for env, old_row in before.items():
            new_row = after.get(env, {})
            for field, old_val in old_row.items():
                if field == "strategies":
                    continue
                if new_row.get(field) != old_val:
                    print(f"REFUSING: summary breakdown {key}.{env}.{field} would change",
                          file=sys.stderr)
                    sys.exit(1)

    report = {
        "seed": args.seed,
        "strategies": len(strategies_next),
        "new_strategy_ids": new_ids,
        "leaderboard_rows": len(leaderboard),
        "entrants": len(competitors["entrants"]),
        "competitions": len(competitions["competitions"]),
        "windows": {c["id"]: [c["window_start"], c["window_end"]]
                    for c in competitions["competitions"]},
    }
    if args.check:
        print(json.dumps({"check": "ok", **report}, indent=2))
        return
    _write("strategies.json", strategies_next)
    _write("leaderboard.json", leaderboard)
    _write("competitors.json", competitors)
    _write("competitions.json", competitions)
    _write("summary.json", summary_next)
    print(json.dumps({"written": True, **report}, indent=2))


if __name__ == "__main__":
    main()
