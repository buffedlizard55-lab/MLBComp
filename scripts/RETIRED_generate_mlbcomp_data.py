"""Retired safety stub.

This file is intentionally not a data generator.  Historical odds, results,
liquidity, fills and model performance must come from source-backed pipeline
runs.  Use ``python -m mlbcomp.web.export_static`` after ingest/backtest.
"""
from __future__ import annotations


def main() -> int:
    raise SystemExit(
        "This generator is retired. Run the verified ingest/backtest/export pipeline; "
        "no synthetic MLB competition data is produced."
    )


if __name__ == "__main__":
    main()
