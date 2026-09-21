"""Create a truthful static snapshot from the current database state.

This script never fetches, fabricates or settles data.  It initializes the
catalog/research/control tables and exports their read-only projection.  Run
the explicit fetch/ingest/backtest commands first for a result-bearing build.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlbcomp.engine.catalog import register_catalog
from mlbcomp.sources import register_catalog as register_sources
from mlbcomp.engine.research import build_research
from mlbcomp.verify.checks import run_checks
from mlbcomp.web.export_static import export


def main() -> None:
    register_sources()
    register_catalog()
    build_research()
    run_checks()
    summary = export()
    print(f"built {summary['data_mode']} snapshot: {summary['total_strategies']} strategies, "
          f"{summary['total_simulated_bets']} wager records")


if __name__ == "__main__":
    main()
