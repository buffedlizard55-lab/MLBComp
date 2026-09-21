"""Leakage-aware splits and metrics shared by backtests and research."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TimeSplit:
    train: pd.DataFrame
    validate: pd.DataFrame
    test: pd.DataFrame
    cutoff_train: str | None
    cutoff_validate: str | None


def chronological_split(frame: pd.DataFrame, time_column: str = "decision_time",
                         train_fraction: float = 0.60, validate_fraction: float = 0.20) -> TimeSplit:
    """Split in time order; equal timestamps stay in one partition."""
    if not 0 < train_fraction < 1 or not 0 < validate_fraction < 1 or train_fraction + validate_fraction >= 1:
        raise ValueError("fractions must be positive and leave a test partition")
    if time_column not in frame.columns:
        raise KeyError(time_column)
    ordered = frame.sort_values([time_column, *(["game_pk"] if "game_pk" in frame else [])], kind="mergesort").reset_index(drop=True)
    n = len(ordered)
    if n == 0:
        empty = ordered.copy()
        return TimeSplit(empty, empty, empty, None, None)
    train_end = max(1, min(n - 2, int(n * train_fraction))) if n >= 3 else max(0, int(n * train_fraction))
    val_end = max(train_end, min(n - 1, int(n * (train_fraction + validate_fraction)))) if n >= 2 else n
    # Move a boundary forward so simultaneous records do not leak across a split.
    for boundary_name, index in (("train", train_end), ("validate", val_end)):
        if index <= 0 or index >= n:
            continue
        timestamp = ordered.iloc[index - 1][time_column]
        while index < n and ordered.iloc[index][time_column] == timestamp:
            index += 1
        if boundary_name == "train":
            train_end = index
            val_end = max(val_end, train_end)
        else:
            val_end = index
    train, validate, test = ordered.iloc[:train_end], ordered.iloc[train_end:val_end], ordered.iloc[val_end:]
    return TimeSplit(train, validate, test,
                     str(train.iloc[-1][time_column]) if len(train) else None,
                     str(validate.iloc[-1][time_column]) if len(validate) else None)


def brier_score(probabilities: Iterable[float], outcomes: Iterable[int | float]) -> float | None:
    p = np.asarray(list(probabilities), dtype=float)
    y = np.asarray(list(outcomes), dtype=float)
    if len(p) == 0 or len(p) != len(y):
        return None
    if not np.isfinite(p).all() or not np.isfinite(y).all():
        return None
    return float(np.mean((p - y) ** 2))


def log_loss(probabilities: Iterable[float], outcomes: Iterable[int | float], epsilon: float = 1e-15) -> float | None:
    p = np.asarray(list(probabilities), dtype=float)
    y = np.asarray(list(outcomes), dtype=float)
    if len(p) == 0 or len(p) != len(y):
        return None
    p = np.clip(p, epsilon, 1 - epsilon)
    if not np.isfinite(p).all() or not np.isfinite(y).all():
        return None
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibration_table(probabilities: Iterable[float], outcomes: Iterable[int | float], bins: int = 10) -> list[dict]:
    p = np.asarray(list(probabilities), dtype=float)
    y = np.asarray(list(outcomes), dtype=float)
    if len(p) == 0 or len(p) != len(y):
        return []
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (p >= low) & (p <= high if high == 1 else p < high)
        if not mask.any():
            continue
        rows.append({"lower": float(low), "upper": float(high), "n": int(mask.sum()),
                     "mean_probability": float(np.mean(p[mask])),
                     "observed_rate": float(np.mean(y[mask]))})
    return rows


def american_pnl(stake: float, american_odds: float, result: str) -> float:
    """Pure settlement math; it does not validate that the price was observed."""
    result = result.upper()
    if result == "P" or result == "V":
        return 0.0
    if result not in {"W", "L"}:
        raise ValueError("result must be W, L, P or V")
    if result == "L":
        return -float(stake)
    odds = float(american_odds)
    return float(stake) * (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def clv_probability(entry_american: float, closing_american: float) -> float | None:
    """Positive means the entry was more favorable than the close."""
    from ..config import am_to_prob
    a, c = am_to_prob(entry_american), am_to_prob(closing_american)
    if not (math.isfinite(a) and math.isfinite(c)):
        return None
    return float(c - a)


def verdict(n: int, roi: float | None = None, lower_ci: float | None = None) -> str:
    """Conservative sample-size gate for promotion/rejection."""
    if n < 30:
        return "INSUFFICIENT_SAMPLE"
    if roi is None:
        return "EVALUATED"
    if lower_ci is not None and lower_ci > 0:
        return "PROMOTE_CANDIDATE"
    if roi <= -0.02:
        return "REJECT_CANDIDATE"
    return "INCONCLUSIVE"
