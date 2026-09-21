"""Persist the versioned strategy library without overwriting prior versions."""
from __future__ import annotations

from .. import db
from .strategies import Strategy, build_catalog


def register_catalog(strategies: list[Strategy] | None = None) -> list[Strategy]:
    strategies = strategies or build_catalog()
    db.init_db()
    with db.db() as conn:
        for strategy in strategies:
            conn.execute(
                "INSERT OR IGNORE INTO strategies "
                "(strategy_id,name,env,model,market,hypothesis,status,created_at,notes) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (strategy.sid, strategy.name, strategy.env, strategy.model, strategy.market,
                 strategy.hypothesis, strategy.status, db.utcnow(), strategy.notes),
            )
            version_id = f"{strategy.sid}_{strategy.version}"
            conn.execute(
                "INSERT OR IGNORE INTO strategy_versions "
                "(strategy_version_id,strategy_id,version_label,environment,round_code,hypothesis,"
                "data_requirements,entry_rule,required_price_rule,sizing_rule,settlement_rule,"
                "test_plan,limitations,parent_version,change_summary,created_at,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (version_id, strategy.sid, strategy.version, strategy.env,
                 strategy.env if strategy.env in {"WC", "DS", "LCS", "WS"} else None,
                 strategy.hypothesis, db.jdump(list(strategy.data_requirements)),
                 strategy.entry_rule, strategy.required_price_rule, strategy.sizing_rule,
                 strategy.settlement_rule, strategy.test_plan, strategy.limitations,
                 strategy.parent_version, "Initial version", db.utcnow(), strategy.status),
            )
            conn.execute(
                "INSERT OR IGNORE INTO model_versions "
                "(version_id,model,env,created_at,config_json,notes,parent_version,feature_cutoff_rule,status) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (version_id, strategy.model, strategy.env, db.utcnow(),
                 db.jdump({"market": strategy.market, "min_edge": strategy.min_edge,
                           "data_requirements": strategy.data_requirements}),
                 "Versioned strategy model; no superiority assumed", strategy.parent_version,
                 "all features available at or before decision timestamp", strategy.status),
            )
    return strategies


if __name__ == "__main__":
    registered = register_catalog()
    print(f"registered {len(registered)} strategy hypotheses")
