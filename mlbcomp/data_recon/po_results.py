"""Retired postseason reconstruction module.

MLBComp never reconstructs game winners from a series score.  If a postseason
row is missing or conflicting, it is placed in the issue queue and excluded.
Use ``mlbcomp.features.po_corpus`` with a validated source-backed schedule.
"""
from __future__ import annotations


def build_reconstructed_corpus(*_args, **_kwargs):
    raise RuntimeError(
        "Postseason reconstruction is disabled: missing winners must remain unavailable, "
        "not inferred from a series result."
    )


if __name__ == "__main__":
    build_reconstructed_corpus()
